"""Capture README screenshots against synthetic, local-only preview data."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "frontend"))
from preview_server import PreviewHTTPServer  # noqa: E402


JOBS = [
    ("北辰科技", "AI 应用工程师", "上海", "人工智能", "官网", 94),
    ("明曜数据", "数据平台开发工程师", "北京", "软件研发", "官网", 91),
    ("星图智能", "算法工程师", "深圳", "人工智能", "官网", 88),
    ("云帆网络", "后端开发工程师", "杭州", "软件研发", "官网", 82),
    ("远山科技", "产品研发工程师", "南京", "产品研发", "官网", 76),
]


def job_rows() -> list[dict]:
    rows = []
    for index, (company, title, city, category, platform, score) in enumerate(JOBS, 1):
        rows.append({
            "id": f"demo-job-{index}", "company_id": f"demo-company-{index}",
            "organization_id": f"demo-company-{index}", "company_name": company,
            "title": title, "city": city, "category_label": category,
            "platform": platform, "match_score": score, "analysis_status": "scored",
            "detail_url": f"https://example.test/jobs/{index}",
            "matched_directions": ["2027 届校招", category],
            "summary": "结合示例技能与岗位要求生成的匹配摘要。",
            "advantages": ["相关项目经历", "技术方向匹配"],
            "gaps": ["进一步了解业务场景"],
        })
    return rows


def application_rows() -> list[dict]:
    stages = ["applied", "applied", "written", "interview1", "offer", "rejected"]
    rows = []
    for index, ((company, title, *_), stage) in enumerate(zip(JOBS + [JOBS[0]], stages), 1):
        rows.append({
            "id": f"demo-application-{index}", "company_name": company,
            "job_title": title, "stage": stage, "stage_history": [],
            "updated_at": "2026-10-01T09:00:00Z",
        })
    return rows


def main() -> None:
    output = ROOT / "docs" / "assets"
    output.mkdir(parents=True, exist_ok=True)
    jobs, applications = job_rows(), application_rows()
    server = PreviewHTTPServer(port=8898, pure_mock=True)
    conversation = server.mock_state.create_thread()
    conversation["title"] = "今日求职安排"
    server.mock_state.run_turn(conversation["id"], "帮我梳理今天需要处理的求职事项")
    conversation["turns"][0]["items"][1]["text"] = (
        "今天建议先查看高匹配岗位，再核对笔试和面试安排。"
        "投递记录中有 1 项笔试和 1 项面试；处理前请打开原始邮件确认时间。"
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def route_jobs(route) -> None:
        query = parse_qs(urlsplit(route.request.url).query)
        first_seen = "first_seen_on" in query
        selected = jobs[:3] if first_seen else jobs
        payload = {
            "items": selected, "total": len(selected), "featured": jobs[:3],
            "summary_included": True,
            "stats": {"jobs": len(jobs), "companies": len(JOBS), "high_match": 3,
                      "pending": 0, "jd_incomplete": 1, "excluded": 0},
            "facets": {"companies": [], "categories": {}, "platforms": []},
        }
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(payload, ensure_ascii=False))

    def route_applications(route) -> None:
        query = parse_qs(urlsplit(route.request.url).query)
        term = query.get("query", [""])[0]
        stages = query.get("stages", [])
        matching = [item for item in applications
                    if term in item["company_name"] + item["job_title"]]
        filtered = [item for item in matching if not stages or item["stage"] in stages]
        offset = int(query.get("offset", ["0"])[0])
        limit = int(query.get("limit", ["50"])[0])
        payload = {"items": filtered[offset:offset + limit], "total": len(filtered),
                   "unfiltered_total": len(applications),
                   "stage_counts": {stage: sum(item["stage"] == stage for item in matching)
                                    for stage in set(item["stage"] for item in matching)}}
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(payload, ensure_ascii=False))

    def route_configuration(route) -> None:
        payload = {
            "settings": {"llm_enabled": True, "codex_runtime_enabled": True,
                         "mail_enabled": True, "mail_imap_port": 993,
                         "mail_imap_mailbox": "INBOX"},
            "profile": {"skills": [], "matching": {"title_keywords": ["工程师"]},
                        "scope": {"industry_groups": ["internet-tech"]}},
            "secrets": {"llm_api_key": True},
            "model_connections": [], "options": {"industry_groups": [], "mail_providers": []},
            "module_readiness": {"assistant": {"status": "ready"}},
            "onboarding": {"ready": True},
        }
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(payload, ensure_ascii=False))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("RECRUITOPS_TEST_BROWSER_EXECUTABLE") or None,
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1040}, device_scale_factor=1)
            page.route("**/api/jobs/browse?*", route_jobs)
            page.route("**/api/applications/page?*", route_applications)
            page.route("**/api/local-ui/configuration/read", route_configuration)
            page.goto("http://127.0.0.1:8898/", wait_until="networkidle")
            page.add_style_tag(content="#recruitops-preview-banner { display: none !important; }")

            page.locator('#sidebar [data-view="jobs"]').click()
            page.locator("#jobs-table-body tr").first.wait_for()
            page.screenshot(path=str(output / "readme-jobs.png"), animations="disabled")

            page.locator('#sidebar [data-view="applications"]').click()
            page.locator(".application-card").first.wait_for()
            page.screenshot(path=str(output / "readme-applications.png"), animations="disabled")

            page.locator('#sidebar [data-view="assistant"]').click()
            page.locator('.conversation-item[title="今日求职安排"]').click()
            page.locator("#assistant-messages .message").nth(1).wait_for()
            page.screenshot(path=str(output / "readme-assistant.png"), animations="disabled")

            page.locator('#sidebar [data-view="mail"]').click()
            page.locator('[data-mail-view="inbox"]').click()
            page.locator("#mail-list .mail-row").first.wait_for()
            page.screenshot(path=str(output / "readme-mail.png"), animations="disabled")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
