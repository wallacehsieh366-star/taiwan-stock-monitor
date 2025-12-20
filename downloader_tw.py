# -*- coding: utf-8 -*-
import os
import time
import threading
import requests
import pandas as pd
import yfinance as yf
import concurrent.futures
from io import StringIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from pathlib import Path

# ========== 核心參數設定 ==========
MARKET_CODE = "tw-share"
DATA_SUBDIR = "dayK"
PROJECT_NAME = "台股日K資料下載器"

# 路徑設定：確保相對於專案目錄
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", MARKET_CODE, DATA_SUBDIR)
LOG_DIR = os.path.join(BASE_DIR, "logs", PROJECT_NAME)
CKPT_FILE = os.path.join(LOG_DIR, "checkpoint_tw.csv")

MAX_WORKERS = 8       # 下載執行緒數量
MIN_FILE_SIZE = 100   # 有效檔案最小位元組
AUTO_ADJUST = False   # yfinance 價格調整

# 確保目錄存在
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

def log(msg: str):
    now = pd.Timestamp.now()
    print(f"{now:%H:%M:%S}: {msg}")

def safe_filename(s: str) -> str:
    return (s.replace("/", "_").replace("\\", "_").replace(":", "_")
              .replace("*", "_").replace("?", "_").replace('"', "_")
              .replace("<", "_").replace(">", "_").replace("|", "_"))

def parse_item(item: str):
    """解析 '代號&名稱' 格式"""
    if '&' in item:
        tkr, nm = item.split('&', 1)
    else:
        tkr, nm = item.strip(), "未知股票"
    return tkr.strip(), nm.strip()

def get_full_stock_list():
    """
    專業版爬蟲：從證交所抓取全市場股票清單 (含上市、上櫃、創新板、ETF)
    """
    url_configs = [
        {'name': 'listed', 'url': 'https://isin.twse.com.tw/isin/class_main.jsp?market=1&issuetype=1&Page=1&chklike=Y', 'suffix': '.TW'},
        {'name': 'otc', 'url': 'https://isin.twse.com.tw/isin/class_main.jsp?market=2&issuetype=4&Page=1&chklike=Y', 'suffix': '.TWO'},
        {'name': 'etf', 'url': 'https://isin.twse.com.tw/isin/class_main.jsp?owncode=&stockname=&isincode=&market=1&issuetype=I&industry_code=&Page=1&chklike=Y', 'suffix': '.TW'},
        {'name': 'tw_innovation', 'url': 'https://isin.twse.com.tw/isin/class_main.jsp?owncode=&stockname=&isincode=&market=C&issuetype=C&industry_code=&Page=1&chklike=Y', 'suffix': '.TW'},
        {'name': 'otc_innovation', 'url': 'https://isin.twse.com.tw/isin/class_main.jsp?owncode=&stockname=&isincode=&market=A&issuetype=C&industry_code=&Page=1&chklike=Y', 'suffix': '.TWO'},
    ]

    all_stock_names = []
    
    def fetch_api(config):
        try:
            time.sleep(0.3)
            resp = requests.get(config['url'], timeout=15)
            df = pd.read_html(StringIO(resp.text), header=0)[0]
            items = []
            for _, row in df.iterrows():
                code = str(row['有價證券代號']).strip()
                name = str(row['有價證券名稱']).strip()
                # 過濾掉權證與非股票類（代號通常 > 5 碼）
                if code and len(code) <= 5:
                    items.append(f"{code}&{name}")
            return items
        except Exception as e:
            print(f"❌ 抓取 {config['name']} 失敗: {e}")
            return []

    log("🌐 啟動多執行緒爬蟲獲取最新台股名單...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_api, cfg) for cfg in url_configs]
        for f in concurrent.futures.as_completed(futures):
            all_stock_names.extend(f.result())

    final_list = list(set(all_stock_names))
    log(f"✅ 成功獲取全市場清單：共 {len(final_list)} 檔標的")
    return final_list

def build_checkpoint(items):
    rows = []
    for it in items:
        tkr, nm = parse_item(it)
        out_path = os.path.join(DATA_DIR, f"{tkr}_{safe_filename(nm)}.csv")
        status = "skipped" if os.path.exists(out_path) and os.path.getsize(out_path) > MIN_FILE_SIZE else "pending"
        rows.append((tkr, nm, status, ""))
    df = pd.DataFrame(rows, columns=["ticker", "name", "status", "last_error"])
    df.to_csv(CKPT_FILE, index=False, encoding='utf-8-sig')
    return df

def download_stock_data(row):
    ticker_id, name = row["ticker"], row["name"]
    # 自動補足 yfinance 後綴
    yf_ticker = ticker_id
    if ".TW" not in yf_ticker.upper() and ".TWO" not in yf_ticker.upper():
        # 簡單判定：一般上市股票 4 碼補 .TW，這部分由 get_full_stock_list 處理更好
        # 但這裡加入一個保險機制
        yf_ticker = f"{ticker_id}.TW" 

    try:
        out_path = os.path.join(DATA_DIR, f"{ticker_id}_{safe_filename(name)}.csv")
        if os.path.exists(out_path) and os.path.getsize(out_path) > MIN_FILE_SIZE:
            return {"ticker": ticker_id, "status": "skipped", "err": ""}

        tk = yf.Ticker(yf_ticker)
        hist = tk.history(period="2y", auto_adjust=AUTO_ADJUST)

        if hist is None or hist.empty:
            # 針對下市標的，我們也標記為 skipped 避免重複抓取浪費時間
            return {"ticker": ticker_id, "status": "skipped", "err": "empty_data"}

        hist.reset_index(inplace=True)
        hist.columns = [c.lower() for c in hist.columns]
        hist.to_csv(out_path, index=False, encoding='utf-8-sig')
        return {"ticker": ticker_id, "status": "success", "err": ""}

    except Exception as e:
        return {"ticker": ticker_id, "status": "failed", "err": str(e)}

def main():
    """主進入點"""
    stockname_list = get_full_stock_list()
    
    if not stockname_list and os.path.exists(CKPT_FILE):
        ckpt = pd.read_csv(CKPT_FILE)
        log(f"🔁 載入既有續傳點：{len(ckpt)} 檔")
    elif not stockname_list:
        log("❌ 無法獲取股票清單且無續傳紀錄，終止執行。")
        return
    else:
        # 如果有新抓到的清單，則建立/更新 Checkpoint
        ckpt = build_checkpoint(stockname_list)
        log(f"🆕 已同步最新清單，開始檢查下載狀態...")

    todo = ckpt[ckpt["status"].isin(["pending", "failed"])].copy()
    
    if len(todo) == 0:
        log("🎉 台股數據已就緒，無需下載。")
        return

    log(f"🚀 開始下載 {len(todo)} 支標的日K資料...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_row = {executor.submit(download_stock_data, r): r for _, r in todo.iterrows()}
        pbar = tqdm(total=len(todo), desc="下載進度")
        
        for future in as_completed(future_to_row):
            res = future.result()
            mask = (ckpt["ticker"] == res["ticker"])
            ckpt.loc[mask, ["status", "last_error"]] = [res["status"], res["err"]]
            # 每 10 檔更新一次 CSV 檔案，避免意外中斷丟失紀錄
            if pbar.n % 10 == 0:
                ckpt.to_csv(CKPT_FILE, index=False, encoding='utf-8-sig')
            pbar.update(1)
        pbar.close()

    ckpt.to_csv(CKPT_FILE, index=False, encoding='utf-8-sig')
    log("📊 下載任務執行完畢。")

if __name__ == "__main__":
    main()
