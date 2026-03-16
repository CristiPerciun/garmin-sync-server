# Verifica Login Garmin Connect

## Stato: ✅ Pronto per il login

L'endpoint `POST /garmin/connect` è configurato correttamente per ricevere le richieste dall'app Flutter.

## Checklist

| Componente | Stato |
|------------|-------|
| Endpoint POST /garmin/connect | ✅ |
| Modello uid, email, password | ✅ |
| Validazione Garmin Connect | ✅ |
| Scrittura Firestore garmin_linked | ✅ |
| Salvataggio token per sync | ✅ |
| Health check GET / | ✅ |
| Fly.io internal_port 8080 | ✅ |
| Dockerfile CMD | ✅ |

## URL per l'app Flutter

```
https://garmin-sync-server.fly.dev/garmin/connect
```

## Test richiesta (curl)

```bash
curl -X POST https://garmin-sync-server.fly.dev/garmin/connect \
  -H "Content-Type: application/json" \
  -d '{"uid":"test-uid","email":"tua@email.com","password":"tua-password"}'
```

## Secrets Fly.io richiesti

```bash
fly secrets set FIREBASE_CREDENTIALS='{"type":"service_account",...}'
```

## Nota: Cold start

Con `auto_stop_machines = 'stop'`, la prima richiesta dopo inattività può richiedere 30-60 secondi. L'app Flutter ha timeout 60s.
