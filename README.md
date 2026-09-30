# WebRTC Camera Viewer - Fixed

Perbaikan:
- Sesi lama dibersihkan pada Socket.IO `disconnect`, sehingga error "Room sedang dipakai sesi lain" tidak tertinggal setelah tab ditutup.
- Viewer/target dapat bergabung ulang ke room setelah koneksi sebelumnya putus.
- Signaling offer/answer/ICE diperketat.
- Ditambah beberapa STUN server.
- Viewer menampilkan status ICE/WebRTC dan memasang `ontrack` secara eksplisit.
- Tetap membutuhkan izin kamera dari pengguna HP.

## Jalankan

```cmd
winget install --id Cloudflare.cloudflared
```

Lalu:

```cmd
cd /d D:\Cyber\WebRTC_Camera_Viewer_Fixed
run.bat
```

Program menghasilkan dua URL HTTPS.

Catatan:
STUN tidak menjamin koneksi pada semua NAT/firewall. Pada jaringan yang tidak memungkinkan direct peer-to-peer, WebRTC dapat membutuhkan TURN server.
"# WebRTC-Remote-Camera-Viewer" 
