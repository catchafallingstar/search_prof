import os
import threading
_ctx = threading.local()

def set_radar_context(session_id: str, lang_code: str):
    """Binds the session and language to the current active thread."""
    _ctx.session_id = session_id
    _ctx.lang_code = lang_code

def get_radar_session() -> str:
    return getattr(_ctx, "session_id", "default_session")

def get_radar_lang() -> str:
    return getattr(_ctx, "lang_code", "en").lower()
TEXT = {
    "English": {
        "title": "🎯 ScholarRadar | Academic Hiring Radar",
        "caption": "⚡ Decision Radar powered by OpenAlex Papers + NSF Grants + Cleaned Recruiting Signals.",
        "stat_profs": "🔥 High-Intent Professors",
        "stat_signals": "📢 Valid Hiring Signals",
        "stat_funded": "💰 Grants-Backed Projects",
        "sidebar_title": "🔍 Signal Filter Console",
        "lang_selector": "🌐 Language / 语言",
        "min_score": "Minimum Hiring Score",
        "filter_inst": "Filter by Target University",
        "filter_kw": "Filter by Keywords (e.g. LLM, Postdoc, Schmidt)",
        "kw_placeholder": "Type keyword...",
        "list_title": "📋 Matched Professors ({count})",
        "badge_high": "🔥 [Extremely High Chance]",
        "badge_medium": "⚡ [Active Hiring Signal]",
        "badge_low": "💡 [Potential Signal]",
        "homepage": "🌐 **Homepage**",
        "no_homepage": "🌐 **Homepage**: *Not Found*",
        "signals_section": "##### 💬 Captured & Filtered Signals:",
        "source_link": "🔗 [View Raw Source]",
        "no_data": "⚠️ No hiring signals in database. Please run scraper scripts first!",
        "unit_prof": "",
        "unit_signal": "",
        "unit_funded": "",
        "domain_selector": "🎯 Switch Research Domain",
        "mine_title": "🚀 Mine New Domain",
        "mine_caption": "Can't find your field? Type a keyword to start full-web mining!",
        "mine_input": "Enter Research Domain:",
        "mine_placeholder": "e.g.: Robotics",
        "mine_max_papers": "Max Papers to Fetch",
        "mine_btn": "⚡ Start Real-time Mining",
        "mine_error": "⚠️ Please enter a domain name!",
        "mine_spinner": "Mining data for 【{domain}】, please wait...",
        "mine_success": "🎉 Mining complete for 【{domain}】! Refreshing...",
        "data_mgmt_title": "💾 Data Management",
        "export_btn": "📥 Download Results (CSV)",
        "export_no_data": "ℹ️ No data available to download.",
        "clear_popover": "🗑️ Clear Database",
        "clear_warning": "⚠️ This will permanently delete all stored professors, papers, grants, and hiring signals!",
        "clear_confirm": "Confirm & Delete All Data",
        "clear_success": "🎉 Database cleared successfully!",
        "clear_error": "❌ Failed to clear database: {error}",
        "tip_refresh": "💡 **Tip:** Refreshing the page will not clear saved data. To start a fresh search, scroll down the left sidebar and click **'Clear Database'**.",
        "stop_btn": "🛑 Cancel / Abort Scan",
        "stop_requested": "🛑 Scan cancellation requested... Safely closing database connections.",
        "start_radar": "🚀 Starting Multi-Channel High-Sensitivity Radar, Target: {count} Professors\n",
        "skip_non_academic": "⏭️ [Skipping Non-Academic] {name} ({institution})",
        "analyzing": "👤 Analyzing: {name} ({institution})",
        "parsing_homepage": "   🌐 Parsing Homepage: {url}",
        "hit_homepage": "   └─ 🔥 [Homepage Hiring Banner{roles}{funding}!] \"{quote}...\" (Score +{score})",
        "no_hiring_verb": "   └─ ⚪ No hiring keywords found on homepage or social channels",
        "no_homepage": "   └─ ⚠️ Compliant academic homepage not found",
        "scan_complete": "\n🎉 Scan Complete! Captured {hits} high-purity hiring signals.",
        "hit_social": "   └─ 🔥 [Web / Press Hit{roles}{funding}!] \"{quote}...\" (Score +{score})",
        "stop_success": "⚠️ Mining process was cancelled by user. Partial progress saved!",
        #fetch_prof
        "start_search": "🔎 Starting OpenAlex search for 2024-2026 US papers in 【{domain}】...",
        "api_failed": "❌ OpenAlex API request failed: {error}",
        "papers_found": "📦 Successfully retrieved {count} relevant papers, starting parsing...",
        "saved_prof": "✅ [Saved] Prof: {name} | Inst: {inst}",
        "process_complete": "\n🎉 Processing complete! Saved/Updated {count} US professors and papers.",
        #check_grants
        "start_query": "💰 Starting NSF grants query for {count} professors...",
        "query_failed": "⚠️ Failed to query NSF grants for {name}: {error}",
        "hit_grant": "💵 [Grant Hit] {name} ({inst}) found {count} NSF grants, hiring_score +{score}",
        "sync_complete": "\n🎉 NSF grants sync complete!",
        "crii_start": "🚀 Discovering brand new Assistant Professors from NSF CRII awards...",
        "crii_skip": "⚠️ Phase 1 CRII Discovery skipped: {error}",
        #search bypass: 
        "search_bypassed": "⚠️ Search bypassed: {error}",
        "phase2_start": "🌐 Running Phase 2: Searching web for unlisted new APs in {domain}...",
        #steps: 
        
        "step1_lbl": "🔍 Step 1/3: Fetching US professors via OpenAlex API...",
        "step2_lbl": "💰 Step 2/3: Cross-referencing NSF Grants database...",
        "step3_lbl": "⚡ Step 3/3: Scanning web, social & personal homepage recruiting signals...",
        "step3_aborted": "🛑 Step 3 Aborted",
    },
    "中文": {
        "title": "🎯 ScholarRadar | 学术导师招人雷达",
        "caption": "⚡ 基于 OpenAlex 顶会论文 + NSF 真实经费 + 社交/主页招人标语语义清洗的套磁决策看板",
        "stat_profs": "🔥 捕获高意向导师总数",
        "stat_signals": "📢 有效招人标语/信号",
        "stat_funded": "💰 具备资金背书项目",
        "sidebar_title": "🔍 信号筛选控制台",
        "lang_selector": "🌐 语言 / Language",
        "min_score": "最低匹配得分 (Hiring Score)",
        "filter_inst": "按目标高校筛选",
        "filter_kw": "关键词过滤 (如: LLM, Schmidt, Postdoc)",
        "kw_placeholder": "输入关键词...",
        "list_title": "📋 匹配到的导师列表 ({count} 位)",
        "badge_high": "🔥 [极高概率招人]",
        "badge_medium": "⚡ [有公开招人意向]",
        "badge_low": "💡 [潜力信号]",
        "homepage": "🌐 **个人主页**",
        "no_homepage": "🌐 **个人主页**: *未检索到*",
        "signals_section": "##### 💬 捕获到的真伪校验信号：",
        "source_link": "🔗 [查看原始来源]",
        "no_data": "⚠️ 数据库中暂无有效招人信号，请先运行数据抓取脚本！",
        "unit_prof": " 位",
        "unit_signal": " 条",
        "unit_funded": " 个",
        "domain_selector": "🎯 切换研究领域 (Research Domain)",
        "mine_title": "🚀 实时挖掘新领域",
        "mine_caption": "没有找到你的领域？输入关键词（如 Computer Vision),一键启动全网挖掘！",
        "mine_input": "输入研究方向:",
        "mine_placeholder": "如: Robotics",
        "mine_max_papers": "最大抓取论文数",
        "mine_btn": "⚡ 启动实时数据挖掘",
        "mine_error": "⚠️ 请先输入研究方向！",
        "mine_spinner": "正在全网深挖【{domain}】领域的导师，请耐心等待...",
        "mine_success": "🎉【{domain}】数据挖掘完成！页面即将自动刷新...",
        "data_mgmt_title": "💾 数据管理控制台",
        "export_btn": "📥 导出当前结果 (CSV)",
        "export_no_data": "ℹ️ 暂无有效数据可供导出",
        "clear_popover": "🗑️ 清空数据库",
        "clear_warning": "⚠️ 这将永久删除数据库中存储的所有导师、论文、经费及招人信号数据！",
        "clear_confirm": "确认彻底清空所有数据",
        "clear_success": "🎉 数据库已成功清空！",
        "tip_refresh": "💡 **提示：** 刷新页面不会清除当前数据。若需重新进行全新搜索，请下滑左侧控制栏使用 **‘清空数据库’** 功能。",
        "clear_error": "❌ 清空数据库失败: {error}",
        "stop_btn": "🛑 终止 / 取消扫描",
        "stop_requested": "🛑 收到终止请求... 正在安全关闭数据库连接并保存进度。",
        "stop_success": "⚠️ 挖掘任务已被用户取消，已保存部分处理结果！",
        #hiring: 
        "start_radar": "🚀 启动全渠道高灵敏度雷达，目标: {count} 位导师\n",
        "skip_non_academic": "⏭️ [跳过非高校单位] {name} ({institution})",
        "analyzing": "👤 正在分析: {name} ({institution})",
        "parsing_homepage": "   🌐 正在解析主页: {url}",
        "hit_homepage": "   └─ 🔥 [主页捕获招人标语{roles}{funding}!] \"{quote}...\" (Score +{score})",
        "no_hiring_verb": "   └─ ⚪ 主页及社交平台均未命中招人动词",
        "no_homepage": "   └─ ⚠️ 未定位到合规学术主页",
        "hit_social": "   └─ 🔥 [网页/媒体捕获招人标语{roles}{funding}!] \"{quote}...\" (Score +{score})",
        "scan_complete": "\n🎉 扫描完成！共捕获到 {hits} 条高纯度招人信号。",
        #fetch_prof
        "start_search": "🔎 开始从 OpenAlex 检索 {year_range} 年的美国【{domain}】论文...",
        "api_failed": "❌ 请求 OpenAlex API 失败: {error}",
        "papers_found": "📦 成功获取到 {count} 篇相关论文，开始解析入库...",
        "saved_prof": "✅ [成功入库] 导师: {name} | 学校: {inst}",
        "process_complete": "\n🎉 处理完成！共沉淀/更新了 {count} 位美国导师及论文数据。",
        #check_grants
        "start_query": "💰 开始为 {count} 位导师查询 NSF 经费...",
        "query_failed": "⚠️ 查询 {name} 的 NSF 经费失败: {error}",
        "hit_grant": "💵 [经费命中] {name} ({inst}) 查到 {count} 笔 NSF 经费, hiring_score +{score}",
        "sync_complete": "\n🎉 NSF 经费同步完成！",
        "crii_start": "🚀 正在从 NSF CRII 资助中检索新入职助理教授...",
        "crii_skip": "⚠️ 阶段 1 CRII 检索已跳过: {error}",
        #search bypass:
        "search_bypassed": "⚠️ 搜索已跳过: {error}",
        "phase2_start": "🌐 正在运行阶段 2: 正在全网检索【{domain}】领域未入库的新入职 AP...",
        #steps:
        "step1_lbl": "🔍 步骤 1/3: 通过 OpenAlex 获取美国导师...",
        "step2_lbl": "💰 步骤 2/3: 交叉比对 NSF 经费数据库...",
        "step3_lbl": "⚡ 步骤 3/3: 扫描主页及社交平台招人信号...",
        "step3_aborted": "🛑 步骤 3 已终止",
    },
}
TEXT["en"] = TEXT["English"]
TEXT["cn"] = TEXT["中文"]
TEXT["zh"] = TEXT["中文"]

def t(key, **kwargs):
    """Thread-safe Localization Helper"""
    current_lang = get_radar_lang()
    msg_dict = TEXT.get(current_lang, TEXT["en"])
    return msg_dict.get(key, TEXT["en"].get(key, "")).format(**kwargs)