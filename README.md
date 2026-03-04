# Python_FrontEnd

Fullstack app:
- **Backend:** FastAPI (Python 3.11), SQLite (SQLAlchemy), OpenCV optional auto-detection, OPC UA client writes `Zone_1_CMD..Zone_40_CMD`
- **Frontend:** React + Vite (desktop UI), operator views + admin zone editor

## Assets layout (Windows)
Parts thumbnails:
- `C:\assets\parts\<part_name>.png`

Part sections (clean + annotated):
- `C:\assets\sections\<part_name>\section_1.png`
- `C:\assets\sections\<part_name>\section_1_annotated.png`
- ... up to section_4

Notes:
- `<part_name>` uses underscores; UI displays spaces.

## Backend setup (Windows)
```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Backend docs:
- http://localhost:8000/docs

## Frontend setup
```powershell
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

Open: http://localhost:5173

## Admin
Admin routes require an admin key.

Set in `backend/.env`:
- `ADMIN_KEY=...`

Frontend: open `/admin/login`, enter key.

## OPC UA
Set in `backend/.env`:
- `OPCUA_ENDPOINT=opc.tcp://192.168.0.149:4850/Magna_IOServer`

Backend discovers nodes by BrowseName under `Objects`:
- `Zone_1_CMD` ... `Zone_40_CMD`

Writes integer 0/1.