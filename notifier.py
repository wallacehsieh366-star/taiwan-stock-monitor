# -*- coding: utf-8 -*-
import os
import requests
import resend
from datetime import datetime, timedelta

class StockNotifier:
    def __init__(self):
        """
        初始化通知模組
        """
        self.tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.resend_api_key = os.getenv("RESEND_API_KEY")
        
        if self.resend_api_key:
            resend.api_key = self.resend_api_key

    def get_now_time(self):
        """
        獲取台北時間 (UTC+8)
        修正 GitHub Actions 環境下的時區偏差
        """
        # 獲取當前 UTC 時間，並強制增加 8 小時
        # 使用特定格式 YYYY-MM-DD HH:MM
        tw_time = datetime.utcnow() + timedelta(hours=8)
        return tw_time.strftime("%Y-%m-%d %H:%M")

    def send_telegram(self, message):
        """發送即時訊息到 Telegram"""
        if not self.tg_token or not self.tg_chat_id:
            print("⚠️ 缺少 Telegram 設定，跳過發送。")
            return False
        
        ts = self.get_now_time().split(" ")[1] 
        full_message = f"{message}\n\n🕒 <i>Sent at {ts} (台北時間)</i>"
        
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {
            "chat_id": self.tg_chat_id, 
            "text": full_message, 
            "parse_mode": "HTML"
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
        except Exception as e:
            print(f"❌ Telegram 發送失敗: {e}")
            return False

    def send_stock_report(self, market_name, img_data, report_df, text_reports, stats=None):
        """
        整合後的發送函數，支援 95.1% 數據完整度儀表板
        """
        if not self.resend_api_key:
            print("❌ 錯誤：找不到 RESEND_API_KEY")
            return

        # 這裡會調用修正後的 +8 時區時間
        now_str = self.get_now_time()
        
        # 市場識別
        market_upper = market_name.upper()
        # ... (其餘 is_tw, is_us 等識別邏輯) ...
        
        # 建立健康度 HTML (stats 邏輯)
        health_html = ""
        if stats:
            total = stats.get("total", 0)
            success = stats.get("success", 0)
            rate = (success / total * 100) if total > 0 else 0
            
            status_color = "#27ae60" if rate >= 85 else "#f39c12"
            status_text = "數據完整度優良" if rate >= 85 else "部分數據缺失"

            health_html = f"""
            <div style="background-color: #f8f9fa; border: 1px solid #e9ecef; padding: 15px; border-radius: 8px; margin: 20px 0; display: flex; align-items: center;">
                <div style="flex: 1; text-align: center; border-right: 1px solid #dee2e6;">
                    <div style="font-size: 12px; color: #6c757d; margin-bottom: 5px;">市場標的總數</div>
                    <div style="font-size: 20px; font-weight: bold; color: #2c3e50;">{total}</div>
                </div>
                <div style="flex: 1; text-align: center; border-right: 1px solid #dee2e6;">
                    <div style="font-size: 12px; color: #6c757d; margin-bottom: 5px;">成功下載檔案</div>
                    <div style="font-size: 20px; font-weight: bold; color: {status_color};">{success}</div>
                </div>
                <div style="flex: 1; text-align: center; border-right: 1px solid #dee2e6;">
                    <div style="font-size: 12px; color: #6c757d; margin-bottom: 5px;">成功率</div>
                    <div style="font-size: 20px; font-weight: bold; color: {status_color};">{rate:.1f}%</div>
                </div>
                <div style="flex: 1.5; text-align: center; padding-left: 10px;">
                    <div style="font-size: 14px; font-weight: bold; color: {status_color};">{status_text}</div>
                </div>
            </div>
            """

        # 組合 HTML
        html_content = f"""
        <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #333; max-width: 850px; margin: auto; border: 1px solid #eee; padding: 20px; border-radius: 10px;">
            <h2 style="color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; margin-bottom: 10px;">
                🚀 {market_name} 全方位市場監控報表
            </h2>
            <p style="color: #7f8c8d; font-size: 14px; margin-bottom: 20px;">報告生成時間: {now_str} (UTC+8)</p>
            
            {health_html}

            <div style="background-color: #fdfefe; border-left: 5px solid #e74c3c; padding: 10px; margin: 20px 0; font-size: 14px;">
                💡 提示：點擊下方表格中的<b>股票代號</b>，可直接跳轉至查看即時技術線圖。
            </div>
            </div>
        """
        
        # 執行發送 (to_emails 建議改回你的變數或固定值)
        try:
            resend.Emails.send({
                "from": "StockMonitor <onboarding@resend.dev>",
                "to": "grissomlin643@gmail.com",
                "subject": f"🚀 {market_name} 監控報告 - {now_str}",
                "html": html_content,
                "attachments": [] # 放入你的圖片附件
            })
            print(f"✅ {market_name} 報告發送成功 ({now_str})")
        except Exception as e:
            print(f"❌ 發送失敗: {e}")
