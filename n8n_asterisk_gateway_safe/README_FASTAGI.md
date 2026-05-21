# n8n Asterisk Gateway - FastAGI version

This version keeps the Python gateway in the user home directory and does not require copying AGI files into `/var/lib/asterisk/agi-bin`.

Asterisk dialplan must call FastAGI:

```asterisk
[n8n-gateway-call]
exten => s,1,NoOp(N8N Gateway call. SESSION_ID=${SESSION_ID})
 same => n,Answer()
 same => n,AGI(agi://127.0.0.1:4573/n8n_gateway?session_id=${SESSION_ID})
 same => n,Hangup()

exten => h,1,NoOp(N8N Gateway hangup. SESSION_ID=${SESSION_ID})
 same => n,AGI(agi://127.0.0.1:4573/n8n_finalize?session_id=${SESSION_ID}&reason=hangup)
```

Run manually in two SSH sessions:

```bash
cd /home/danil_z/n8n-asterisk-gateway
. .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8088
```

```bash
cd /home/danil_z/n8n-asterisk-gateway
. .venv/bin/activate
python fastagi_server.py
```

Test:

```bash
curl http://127.0.0.1:8088/health
```
