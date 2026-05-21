# n8n Asterisk Gateway MVP

Python/FastAPI шлюз между n8n и Asterisk через AMI. n8n общается только по HTTP/webhook. Asterisk управляется через AMI и AGI.

## Важно про секреты

Этот репозиторий безопасен для загрузки на GitHub: в нём нет реальных паролей, IP, webhook URL и SIP/AMI credentials.

В GitHub можно хранить:

- исходный код `app/`, `agi/`;
- шаблон `.env.example`;
- шаблоны Asterisk-конфигов в `asterisk/`;
- installer `deploy/install_gateway.sh`.

В GitHub нельзя хранить:

- настоящий `.env`;
- AMI пароль;
- SIP пароль;
- SSH пароль;
- production n8n webhook URL, если он считается секретом;
- записи звонков и аудио-файлы.

Файл `.gitignore` уже исключает `.env`, `.venv`, записи и runtime-файлы.

## Поток звонка

1. n8n вызывает `POST /start-call` с номером `7XXXXXXXXXX`, `audio_url` и webhook URL.
2. Gateway скачивает аудио, конвертирует в WAV через `ffmpeg`.
3. Gateway делает AMI Originate в канал из `ASTERISK_CHANNEL_TEMPLATE`.
4. Asterisk после ответа запускает AGI.
5. AGI проигрывает WAV, пишет ответ собеседника в WAV, отправляет путь к записи в gateway.
6. Gateway отправляет WAV + metadata в n8n webhook.
7. n8n отвечает `{"action":"continue","audio_url":"..."}` или `{"action":"hangup"}`.
8. Цикл продолжается до hangup/finalize.

## Локальная структура

```text
app/                         # FastAPI gateway
agi/                         # Asterisk AGI scripts
asterisk/                    # example Asterisk configs, no real secrets
deploy/install_gateway.sh    # installer template, no hard-coded secrets
systemd/                     # service example
.env.example                 # environment template, no real secrets
.gitignore                   # excludes .env and generated data
requirements.txt
```

## Настройка на сервере

На сервере настоящий `.env` создаётся из шаблона:

```bash
cd /opt/n8n_asterisk_gateway
cp .env.example .env
nano .env
```

Пример значений, которые админ заполняет только на сервере:

```env
AMI_HOST=<ASTERISK_AMI_HOST>
AMI_PORT=5038
AMI_USERNAME=<AMI_USERNAME>
AMI_SECRET=<AMI_SECRET>
ASTERISK_TRUNK=<ASTERISK_TRUNK_NAME>
ASTERISK_CHANNEL_TEMPLATE=SIP/{phone}@<ASTERISK_TRUNK_NAME>
GATEWAY_BASE_URL=http://127.0.0.1:8088
```

Для старого `chan_sip` иногда нужен peer-first формат:

```env
ASTERISK_CHANNEL_TEMPLATE=SIP/<ASTERISK_TRUNK_NAME>/{phone}
```

## Установка через deploy/install_gateway.sh

Installer не содержит секретов. Секреты передаются админом в переменных окружения в момент запуска:

```bash
REPO_URL='https://github.com/YOUR_USERNAME/n8n-asterisk-gateway.git' \
PROJECT_USER='danil_z' \
AMI_HOST='<ASTERISK_AMI_HOST>' \
AMI_USERNAME='<AMI_USERNAME>' \
AMI_SECRET='<AMI_SECRET>' \
ASTERISK_TRUNK='<ASTERISK_TRUNK_NAME>' \
ASTERISK_CHANNEL_TEMPLATE='SIP/{phone}@<ASTERISK_TRUNK_NAME>' \
sudo -E bash deploy/install_gateway.sh
```

Если installer скачивается напрямую с GitHub raw URL:

```bash
curl -fsSL 'https://raw.githubusercontent.com/YOUR_USERNAME/n8n-asterisk-gateway/main/deploy/install_gateway.sh' -o /tmp/install_gateway.sh
REPO_URL='https://github.com/YOUR_USERNAME/n8n-asterisk-gateway.git' \
PROJECT_USER='danil_z' \
AMI_HOST='<ASTERISK_AMI_HOST>' \
AMI_USERNAME='<AMI_USERNAME>' \
AMI_SECRET='<AMI_SECRET>' \
ASTERISK_TRUNK='<ASTERISK_TRUNK_NAME>' \
ASTERISK_CHANNEL_TEMPLATE='SIP/{phone}@<ASTERISK_TRUNK_NAME>' \
sudo -E bash /tmp/install_gateway.sh
```

## Asterisk templates

### AMI user

Файл `/etc/asterisk/manager.conf`:

```ini
[n8n_gateway]
secret = <AMI_SECRET>
read = system,call,log,verbose,command,agent,user,originate
write = system,call,log,verbose,command,agent,user,originate
permit = 127.0.0.1/255.255.255.255
; permit = <GATEWAY_SERVER_IP>/255.255.255.255
```

### Dialplan

Файл `/etc/asterisk/extensions.conf`:

```asterisk
[n8n-gateway-call]
exten => s,1,NoOp(N8N Gateway call. SESSION_ID=${N8N_SESSION_ID})
 same => n,Answer()
 same => n,AGI(n8n_gateway_agi.py,${N8N_SESSION_ID},${N8N_GATEWAY_BASE_URL})
 same => n,Hangup()

exten => h,1,NoOp(N8N Gateway hangup. SESSION_ID=${N8N_SESSION_ID})
 same => n,AGI(n8n_gateway_finalize.py,${N8N_SESSION_ID},${N8N_GATEWAY_BASE_URL})
```

## Запуск/проверка

```bash
curl http://127.0.0.1:8088/health
```

Ожидаемый ответ:

```json
{"status":"ok"}
```

Тестовый запрос:

```bash
curl -X POST http://127.0.0.1:8088/start-call \
  -H 'Content-Type: application/json' \
  -d '{
    "phone":"79991234567",
    "audio_url":"https://example.com/hello.mp3",
    "n8n_webhook_url":"https://n8n.example.com/webhook/asterisk-turn",
    "finalize_webhook_url":"https://n8n.example.com/webhook/asterisk-finalize",
    "metadata":{"lead_id":"123"}
  }'
```

## Обновление после первой установки

Если админ установил `/usr/local/bin/update-n8n-asterisk-gateway` и дал ограниченное sudo-право, обновление делается так:

```bash
ssh danil_z@<ASTERISK_SERVER_IP>
sudo /usr/local/bin/update-n8n-asterisk-gateway
```

## Ответ n8n webhook на turn

Продолжить:

```json
{
  "action": "continue",
  "audio_url": "https://example.com/next.mp3"
}
```

Завершить:

```json
{
  "action": "hangup",
  "reason": "finished"
}
```
