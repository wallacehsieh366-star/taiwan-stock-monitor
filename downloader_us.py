# -*- coding: utf-8 -*-
import os
import time
import random
import json
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime
from io import StringIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from pathlib import Path

# ========== 核心參數設定 ==========
MARKET_CODE = "us-share"
DATA_SUBDIR = "dayK"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", MARKET_CODE, DATA_SUBDIR)
# 清單快取路徑
CACHE_LIST_PATH = os.path.join(BASE_DIR, "us_stock_list_cache.json")

# 美股標的多，建議 4-5 執行緒，並配合隨機延遲
MAX_WORKERS = 4 
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

def log(msg: str):
    print(f"{pd.Timestamp.now():%H:%M:%S}: {msg}")

def classify_security(name: str, is_etf: bool) -> str:
    """過濾邏輯：排除 ETF 與 衍生品 (Warrant, Rights 等)"""
    if is_etf: return "Exclude"
    n_upper = str(name).upper()
    exclude_keywords = ["WARRANT", "RIGHTS", "UNIT", "PREFERRED", "DEBENTURE"]
    if any(kw in n_upper for kw in exclude_keywords): return "Exclude"
    return "Common Stock"

def get_full_stock_list():
    """
    ⚡ 快取化清單獲取：優先從 Nasdaq 官網抓取清單，並過濾出普通股
    """
    if os.path.exists(CACHE_LIST_PATH):
        file_mtime = os.path.getmtime(CACHE_LIST_PATH)
        # 如果檔案是今天產生的，就直接載入
        if datetime.fromtimestamp(file_mtime).date() == datetime.now().date():
            log("📦 偵測到今日已緩存美股清單，直接載入...")
            with open(CACHE_LIST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)

    log("📡 緩存失效，開始從官網獲取美股普通股清單...")
    all_rows = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    # 1. NASDAQ 市場清單
    try:
        r1 = requests.get("https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt", timeout=15, headers=headers)
        df1 = pd.read_csv(StringIO(r1.text), sep="|")
        df1 = df1[df1["Test Issue"] == "N"].dropna(subset=["Symbol", "Security Name"])
        for _, row in df1.iterrows():
            name = str(row["Security Name"])
            if classify_security(name, row["ETF"] == "Y") == "Common Stock":
                symbol = str(row['Symbol']).strip().replace('$', '-')
                all_rows.append(f"{symbol}&{name}")
    except Exception as e: log(f"⚠️ NASDAQ 獲取失敗: {e}")

    # 2. NYSE 與其餘市場清單
    try:
        r2 = requests.get("https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt", timeout=15, headers=headers)
        df2 = pd.read_csv(StringIO(r2.text), sep="|")
        df2 = df2[df2["Test Issue"] == "N"].dropna(subset=["NASDAQ Symbol", "Security Name"])
        for _, row in df2.iterrows():
            name = str(row["Security Name"])
            if classify_security(name, row["ETF"] == "Y") == "Common Stock":
                symbol = str(row['NASDAQ Symbol']).strip().replace('$', '-')
                all_rows.append(f"{symbol}&{name}")
    except Exception as e: log(f"⚠️ NYSE/Other 獲取失敗: {e}")

    final_list = list(set(all_rows))
    
    if final_list:
        with open(CACHE_LIST_PATH, "w", encoding="utf-8") as f:
            json.dump(final_list, f, ensure_ascii=False)
        log(f"✅ 美股清單更新完成，共 {len(final_list)} 檔普通股。")
        return final_list
    else:
        log("❌ 無法獲取任何美股標的清單。")
        return []

def download_stock_data(item):
    """
    ⚡ 檔案級快取下載邏輯
    """
    try:
        parts = item.split('&', 1)
        if len(parts) < 2: return {"status": "error"}
        yf_tkr, name = parts
        
        # 移除檔名非法字元
        safe_name = "".join([c for c in name if c.isalnum() or c in (' ', '_', '-')]).strip()
        out_path = os.path.join(DATA_DIR, f"{yf_tkr}_{safe_name}.csv")
        
        # ✅ 快取檢查：檢查檔案是否存在且是今天更新的
        if os.path.exists(out_path):
            mtime = datetime.fromtimestamp(os.path.getmtime(out_path)).date()
            if mtime == datetime.now().date() and os.path.getsize(out_path) > 1000:
                return {"status": "exists", "tkr": yf_tkr}

        # --- 若無快取則下載 ---
        time.sleep(random.uniform(0.4, 1.2))
        tk = yf.Ticker(yf_tkr)
        
        for attempt in range(2):
            try:
                hist = tk.history(period="2y", timeout=20)
                if hist is not None and not hist.empty:
                    hist.reset_index(inplace=True)
                    hist.columns = [c.lower() for c in hist.columns]
                    hist.to_csv(out_path, index=False, encoding='utf-8-sig')
                    return {"status": "success", "tkr": yf_tkr}
                if attempt == 1: return {"status": "empty", "tkr": yf_tkr}
            except Exception as e:
                if "Rate limited" in str(e): 
                    time.sleep(random.uniform(20, 40))
            time.sleep(random.uniform(3, 6))

        return {"status": "empty", "tkr": yf_tkr}
    except: 
        return {"status": "error"}

def main():
    items = get_full_stock_list()
    if not items:
        return {"total": 0, "success": 0, "fail": 0}

    log(f"🚀 啟動美股下載任務，目標總數: {len(items)}")
    stats = {"success": 0, "exists": 0, "empty": 0, "error": 0}
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(download_stock_data, it): it for it in items}
        pbar = tqdm(total=len(items), desc="美股下載進度", unit="檔")
        
        for future in as_completed(futures):
            res = future.result()
            stats[res.get("status", "error")] += 1
            pbar.update(1)
            
            # 每成功下載 100 檔額外休息，防止被 Yahoo 封鎖
            if pbar.n % 100 == 0:
                time.sleep(random.uniform(10, 20))
        pbar.close()
    
    # ✨ 重要：構建回傳給 main.py 的統計字典
    report_stats = {
        "total": len(items),
        "success": stats["success"] + stats["exists"],
        "fail": stats["error"] + stats["empty"]
    }
    
    print("\n" + "="*50)
    log(f"📊 美股下載完成報告: {report_stats}")
    print("="*50 + "\n")
    
    return report_stats # 👈 必須 Return 給 main.py

if __name__ == "__main__":
    main()
