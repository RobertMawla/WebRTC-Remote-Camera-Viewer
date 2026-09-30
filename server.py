
import re
import shutil
import socket
import subprocess
import threading
import time
import secrets

from flask import Flask, abort, render_template_string, request
from flask_socketio import SocketIO, emit, join_room

HOST = "127.0.0.1"
PORT = 5000

app = Flask(__name__)
app.config["SECRET_KEY"] = secrets.token_hex(32)

socketio = SocketIO(
    app,
    async_mode="threading",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)

ROOM_ID = secrets.token_urlsafe(24)

# One active viewer and one active target per room.
rooms = {}
rooms_lock = threading.Lock()

PAGE = r"""
<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
:root { color-scheme: dark; font-family: system-ui, sans-serif; }
body {
  margin:0; background:#0f172a; color:#f8fafc; min-height:100vh;
  display:grid; place-items:center;
}
main { width:min(94vw,980px); padding:20px; }
.card {
  background:#1e293b; border:1px solid #334155; border-radius:18px;
  padding:20px; box-shadow:0 16px 50px rgba(0,0,0,.3);
}
h1 { margin:0 0 14px; font-size:24px; }
p { color:#cbd5e1; line-height:1.5; }
.status {
  padding:12px 14px; border-radius:12px; background:#0f172a;
  border:1px solid #334155; margin:12px 0 16px; white-space:pre-wrap;
}
video {
  display:block; width:100%; max-height:72vh; min-height:240px;
  background:#000; border-radius:14px; object-fit:contain;
}
button {
  border:0; border-radius:10px; padding:12px 16px;
  font-size:16px; cursor:pointer; margin-right:8px;
}
#start { background:#22c55e; color:#052e16; }
#stop { background:#ef4444; color:white; }
.muted { color:#94a3b8; font-size:13px; }
</style>
</head>
<body>
<main>
<div class="card">
<h1>{{ title }}</h1>
<div id="status" class="status">{{ initial_status }}</div>

{% if role == "target" %}
<p>
Kamera hanya aktif setelah tombol di bawah ditekan dan izin kamera diberikan
oleh browser.
</p>
<button id="start">Mulai Kamera</button>
<button id="stop" disabled>Hentikan Kamera</button>
<video id="localVideo" autoplay playsinline muted></video>
<p class="muted">Status WebRTC akan ditampilkan di atas.</p>
{% else %}
<p>Viewer akan menampilkan video setelah HP target mengaktifkan kamera.</p>
<video id="remoteVideo" autoplay playsinline controls></video>
<p class="muted">Jika jaringan sangat ketat/NAT tertentu, koneksi WebRTC dapat memerlukan TURN.</p>
{% endif %}
</div>
</main>

<script src="https://cdn.socket.io/4.8.1/socket.io.min.js"></script>
<script>
const ROLE = {{ role|tojson }};
const ROOM = {{ room|tojson }};
const socket = io({
  transports: ["websocket", "polling"],
  reconnection: true,
  reconnectionAttempts: 10
});

let pc = null;
let localStream = null;
let pendingIce = [];
let makingOffer = false;

const statusEl = document.getElementById("status");

function status(msg) {
  console.log(msg);
  statusEl.textContent = msg;
}

function closePeer() {
  if (pc) {
    try { pc.ontrack = null; } catch (_) {}
    try { pc.close(); } catch (_) {}
  }
  pc = null;
  pendingIce = [];
}

function createPeer() {
  const peer = new RTCPeerConnection({
    iceServers: [
      { urls: "stun:stun.cloudflare.com:3478" },
      { urls: "stun:stun.l.google.com:19302" },
      { urls: "stun:stun1.l.google.com:19302" },
      { urls: "stun:stun2.l.google.com:19302" }
    ]
  });

  peer.onicecandidate = (event) => {
    if (event.candidate) {
      socket.emit("ice", {
        room: ROOM,
        candidate: event.candidate
      });
    }
  };

  peer.onicegatheringstatechange = () => {
    status("ICE gathering: " + peer.iceGatheringState);
  };

  peer.oniceconnectionstatechange = () => {
    status("ICE connection: " + peer.iceConnectionState);
  };

  peer.onconnectionstatechange = () => {
    status("WebRTC connection: " + peer.connectionState);
    if (peer.connectionState === "connected") {
      status("Video tersambung.");
    }
    if (peer.connectionState === "failed") {
      status("WebRTC gagal. Coba jaringan lain atau TURN server.");
    }
  };

  peer.onsignalingstatechange = () => {
    console.log("signaling:", peer.signalingState);
  };

  return peer;
}

async function addPendingIce() {
  if (!pc || !pc.remoteDescription) return;
  const copy = pendingIce;
  pendingIce = [];
  for (const candidate of copy) {
    try {
      await pc.addIceCandidate(candidate);
    } catch (e) {
      console.warn("addIceCandidate:", e);
    }
  }
}

socket.on("connect", () => {
  status("Socket tersambung. Mendaftarkan " + ROLE + "...");
  socket.emit("join", { room: ROOM, role: ROLE });
});

socket.on("disconnect", () => {
  status("Socket terputus. Menyambung kembali...");
});

socket.on("room_busy", (data) => {
  status(
    "Sesi ini sedang dipakai tab/perangkat lain.\n" +
    (data && data.role ? "Role aktif: " + data.role : "") +
    "\nTutup tab lama atau jalankan ulang server."
  );
});

socket.on("joined", (data) => {
  status(
    "Terdaftar sebagai " + ROLE +
    (data && data.otherConnected ? ". Pasangan sudah online." : ". Menunggu pasangan...")
  );
});

socket.on("target_ready", async () => {
  if (ROLE !== "viewer") return;
  await startViewerOffer();
});

socket.on("target_stopped", () => {
  if (ROLE === "viewer") {
    const remote = document.getElementById("remoteVideo");
    if (remote) remote.srcObject = null;
    closePeer();
    status("Kamera target dihentikan.");
  }
});

socket.on("offer", async (data) => {
  if (ROLE !== "target" || !localStream || !data || !data.sdp) return;

  try {
    closePeer();
    pc = createPeer();

    localStream.getTracks().forEach(track => pc.addTrack(track, localStream));

    await pc.setRemoteDescription(data.sdp);
    await addPendingIce();

    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);

    socket.emit("answer", {
      room: ROOM,
      sdp: pc.localDescription
    });

    status("Jawaban WebRTC dikirim. Menunggu koneksi video...");
  } catch (e) {
    status("Target gagal membuat answer: " + e.message);
  }
});

socket.on("answer", async (data) => {
  if (ROLE !== "viewer" || !pc || !data || !data.sdp) return;

  try {
    await pc.setRemoteDescription(data.sdp);
    await addPendingIce();
    status("Answer diterima. Membangun koneksi video...");
  } catch (e) {
    status("Viewer gagal menerima answer: " + e.message);
  }
});

socket.on("ice", async (data) => {
  if (!data || !data.candidate) return;

  const candidate = new RTCIceCandidate(data.candidate);

  if (!pc || !pc.remoteDescription) {
    pendingIce.push(candidate);
    return;
  }

  try {
    await pc.addIceCandidate(candidate);
  } catch (e) {
    console.warn("ICE error:", e);
  }
});

async function startViewerOffer() {
  if (ROLE !== "viewer" || makingOffer) return;
  makingOffer = true;

  try {
    closePeer();
    pc = createPeer();

    const receiver = pc.addTransceiver("video", { direction: "recvonly" });

    pc.ontrack = (event) => {
      console.log("track received:", event.track.kind, event.streams);
      const remote = document.getElementById("remoteVideo");
      if (!remote) return;

      if (event.streams && event.streams[0]) {
        remote.srcObject = event.streams[0];
      } else {
        const stream = remote.srcObject || new MediaStream();
        stream.addTrack(event.track);
        remote.srcObject = stream;
      }

      remote.play().then(() => {
        status("Video dari HP target diterima.");
      }).catch(() => {
        status("Video sudah diterima. Tekan Play pada video jika browser menahannya.");
      });
    };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    socket.emit("offer", {
      room: ROOM,
      sdp: pc.localDescription
    });

    status("Offer dikirim ke HP target. Menunggu answer...");
    console.log("viewer receiver:", receiver);
  } catch (e) {
    status("Gagal membuat offer: " + e.message);
  } finally {
    makingOffer = false;
  }
}

if (ROLE === "target") {
  const startBtn = document.getElementById("start");
  const stopBtn = document.getElementById("stop");
  const localVideo = document.getElementById("localVideo");

  startBtn.onclick = async () => {
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        status("Browser tidak menyediakan getUserMedia. Pastikan link HTTPS.");
        return;
      }

      status("Meminta izin kamera...");
      localStream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1280, max: 1920 },
          height: { ideal: 720, max: 1080 },
          frameRate: { ideal: 30, max: 30 }
        },
        audio: false
      });

      localVideo.srcObject = localStream;
      await localVideo.play().catch(() => {});

      startBtn.disabled = true;
      stopBtn.disabled = false;

      socket.emit("target_ready_client", { room: ROOM });
      status("Kamera aktif. Menunggu viewer...");
    } catch (e) {
      status("Kamera gagal dibuka: " + e.name + " - " + e.message);
    }
  };

  stopBtn.onclick = () => {
    if (localStream) {
      localStream.getTracks().forEach(track => track.stop());
      localStream = null;
    }
    localVideo.srcObject = null;
    closePeer();

    startBtn.disabled = false;
    stopBtn.disabled = true;
    socket.emit("target_stopped_client", { room: ROOM });
    status("Kamera dihentikan.");
  };
}
</script>
</body>
</html>
"""

def current_sid():
    return request.sid

def state_for(room):
    return rooms.setdefault(
        room,
        {
            "viewer": None,
            "target": None,
            "target_ready": False,
        }
    )

@app.route("/")
def index():
    return "WebRTC camera server aktif."

@app.route("/viewer/<room>")
def viewer(room):
    if room != ROOM_ID:
        abort(404)
    return render_template_string(
        PAGE,
        role="viewer",
        room=ROOM_ID,
        title="Viewer Kamera",
        initial_status="Menghubungkan..."
    )

@app.route("/target/<room>")
def target(room):
    if room != ROOM_ID:
        abort(404)
    return render_template_string(
        PAGE,
        role="target",
        room=ROOM_ID,
        title="HP Target - Kamera",
        initial_status="Tekan 'Mulai Kamera'."
    )

@socketio.on("join")
def on_join(data):
    room = data.get("room")
    role = data.get("role")

    if room != ROOM_ID or role not in ("viewer", "target"):
        emit("error_message", {"message": "Room/role tidak valid."})
        return

    sid = current_sid()

    with rooms_lock:
        state = state_for(room)
        old_sid = state[role]

        if old_sid and old_sid != sid:
            emit("room_busy", {"role": role})
            return

        state[role] = sid
        other_connected = (
            state["target"] is not None if role == "viewer"
            else state["viewer"] is not None
        )

        target_is_ready = state["target_ready"]

    join_room(room)

    emit("joined", {"otherConnected": other_connected})

    # If viewer joined after target already enabled camera, start negotiation.
    if role == "viewer" and target_is_ready:
        emit("target_ready")

@socketio.on("target_ready_client")
def on_target_ready(data):
    if data.get("room") != ROOM_ID:
        return

    sid = current_sid()

    with rooms_lock:
        state = rooms.get(ROOM_ID)
        if not state or state["target"] != sid:
            return
        state["target_ready"] = True
        viewer_sid = state["viewer"]

    if viewer_sid:
        socketio.emit("target_ready", to=viewer_sid)

@socketio.on("target_stopped_client")
def on_target_stopped(data):
    if data.get("room") != ROOM_ID:
        return

    sid = current_sid()

    with rooms_lock:
        state = rooms.get(ROOM_ID)
        if not state or state["target"] != sid:
            return
        state["target_ready"] = False
        viewer_sid = state["viewer"]

    if viewer_sid:
        socketio.emit("target_stopped", to=viewer_sid)

@socketio.on("offer")
def on_offer(data):
    if data.get("room") != ROOM_ID:
        return

    sid = current_sid()

    with rooms_lock:
        state = rooms.get(ROOM_ID)
        if not state or state["viewer"] != sid:
            return
        target_sid = state["target"]

    if target_sid:
        socketio.emit("offer", {"sdp": data.get("sdp")}, to=target_sid)

@socketio.on("answer")
def on_answer(data):
    if data.get("room") != ROOM_ID:
        return

    sid = current_sid()

    with rooms_lock:
        state = rooms.get(ROOM_ID)
        if not state or state["target"] != sid:
            return
        viewer_sid = state["viewer"]

    if viewer_sid:
        socketio.emit("answer", {"sdp": data.get("sdp")}, to=viewer_sid)

@socketio.on("ice")
def on_ice(data):
    if data.get("room") != ROOM_ID:
        return

    sid = current_sid()

    with rooms_lock:
        state = rooms.get(ROOM_ID)
        if not state:
            return

        if state["viewer"] == sid:
            other_sid = state["target"]
        elif state["target"] == sid:
            other_sid = state["viewer"]
        else:
            return

    if other_sid:
        socketio.emit(
            "ice",
            {"candidate": data.get("candidate")},
            to=other_sid
        )

@socketio.on("disconnect")
def on_disconnect():
    sid = current_sid()

    with rooms_lock:
        state = rooms.get(ROOM_ID)
        if not state:
            return

        changed = False

        if state["viewer"] == sid:
            state["viewer"] = None
            changed = True

        if state["target"] == sid:
            state["target"] = None
            state["target_ready"] = False
            changed = True

        if not changed:
            return

        viewer_sid = state["viewer"]
        target_sid = state["target"]

    if viewer_sid:
        socketio.emit("target_stopped", to=viewer_sid)

    if target_sid:
        socketio.emit(
            "error_message",
            {"message": "Viewer terputus. Menunggu viewer baru."},
            to=target_sid
        )

def wait_for_port(host, port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.25)
    return False

def start_server():
    socketio.run(
        app,
        host=HOST,
        port=PORT,
        debug=False,
        allow_unsafe_werkzeug=True
    )

def start_cloudflared():
    exe = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
    if not exe:
        raise FileNotFoundError(
            "cloudflared tidak ditemukan. Install dengan:\n"
            "winget install --id Cloudflare.cloudflared"
        )

    cmd = [
        exe, "tunnel", "--no-autoupdate",
        "--url", f"http://{HOST}:{PORT}"
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    deadline = time.time() + 45
    public_url = None

    while time.time() < deadline:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                break
            time.sleep(0.1)
            continue

        match = pattern.search(line)
        if match:
            public_url = match.group(0)
            break

    if not public_url:
        process.terminate()
        raise RuntimeError(
            "URL HTTPS Cloudflare tidak ditemukan. "
            "Cek output cloudflared manual."
        )

    return process, public_url

def main():
    print("=" * 74)
    print(" WebRTC CAMERA VIEWER - FIXED")
    print("=" * 74)
    print("Kamera hanya aktif setelah izin kamera diberikan di HP target.")
    print()

    thread = threading.Thread(target=start_server, daemon=True)
    thread.start()

    if not wait_for_port(HOST, PORT):
        raise RuntimeError("Server Python gagal start.")

    tunnel, public_url = start_cloudflared()

    viewer_url = f"{public_url}/viewer/{ROOM_ID}"
    target_url = f"{public_url}/target/{ROOM_ID}"

    print()
    print("VIEWER (PC Anda):")
    print(viewer_url)
    print()
    print("TARGET (HP):")
    print(target_url)
    print()
    print("Gunakan URL yang sama selama program ini berjalan.")
    print("Bila muncul 'Room sedang dipakai', tutup tab lama lalu refresh.")
    print("Ctrl+C untuk berhenti.")
    print("=" * 74)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tunnel.kill()
        print("\nSelesai.")

if __name__ == "__main__":
    main()
