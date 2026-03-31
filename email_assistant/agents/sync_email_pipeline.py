import sys
import logging
from sync_historical_email import fetch_emails, init_db

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

def run_pipeline():
    """ETL 主进程"""
    logger.info("🚀 启动 Email ETL Pipeline...")

    try:
        # 1. 自动初始化表结构
        logger.info("检查并初始化数据库表...")
        init_db()

        # 2. 执行同步
        from config import EMAIL_FETCH_LIMIT
        logger.info(f"正在同步邮件 (最大限制: {EMAIL_FETCH_LIMIT})...")
        
        # 抓取并存储（存储逻辑已内化在 fetch_emails 中）
        emails = fetch_emails(unread_only=True)

        if not emails:
            logger.info("同步完成：没有发现新邮件。")
        else:
            logger.info(f"✅ 成功同步 {len(emails)} 封邮件。")
            for e in emails[:3]:
                logger.info(f"  - [入库] {e['subject'][:40]} (发件人: {e['from_addr']})")

    except Exception as e:
        logger.error(f"❌ Pipeline 运行崩溃: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    run_pipeline()