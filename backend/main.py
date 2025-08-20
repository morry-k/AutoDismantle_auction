# backend/main.py
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging
import traceback

# ===== ログ & アプリ作成（これが一番最初）=====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("app")

app = FastAPI(debug=True)  # ← 先にこれが必要（これより前に include_router を書かない）

# ===== CORS =====
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== 例外をJSONで返す（任意：デバッグ便利）=====
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("UNHANDLED: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "where": "unhandled", "error": str(exc), "traceback": traceback.format_exc()},
    )

# ===== ルーター登録（app 作成の“後”に書く）=====
from api.upload import router as upload_router
from api.admin import router as admin_router

app.include_router(upload_router, prefix="/api")
app.include_router(admin_router)  # admin.py で prefix="/admin" になっているのでこのままでOK

# ===== ヘルスチェック =====
@app.get("/health")
def health():
    return {"ok": True}
