python -m uvicorn app.main:app --reload
.\venv\Scripts\Activate.ps1
uvicorn app.main:app --host 0.0.0.0 --port 5025
rm -rf node_modules
rm package-lock.json
npm install
Then run:

npm run dev -- --host 0.0.0.0 --port 9018
uvicorn app.main:app --host 0.0.0.0 --port 5025

ng serve --host 0.0.0.0 --port 9001 --ssl true --ssl-key /etc/letsencrypt/archive/aspiresystems.in/privkey1.pem --ssl-cert /etc/letsencrypt/archive/aspiresystems.in/fullchain1.pem



nohup uvicorn app.main:app   --host 0.0.0.0   --port 5025   --ssl-certfile /etc/letsencrypt/archive/aspiresystems.in/cert1.pem   --ssl-keyfile /etc/letsencrypt/archive/aspiresystems.in/privkey1.pem
  
uvicorn app.main:app   --host 0.0.0.0   --port 8025   --ssl-certfile /etc/letsencrypt/archive/aspiresystems.in/cert1.pem   --ssl-keyfile /etc/letsencrypt/archive/aspiresystems.in/privkey1.pem


uvicorn app.main:app   --host 0.0.0.0   --port 5030   --ssl-certfile /etc/letsencrypt/archive/aspiresystems.in/cert1.pem   --ssl-keyfile /etc/letsencrypt/archive/aspiresystems.in/privkey1.pem
