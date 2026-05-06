"""
自然语言调度解析器测试
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

from engine.nlp_schedule_parser import parse, is_schedule_intent


class TestIsScheduleIntent:
    def test_normal_text_not_intent(self):
        assert is_schedule_intent("打开记事本") is False
        assert is_schedule_intent("你好") is False
        assert is_schedule_intent("abc") is False

    def test_time_keywords_detect_intent(self):
        assert is_schedule_intent("今晚9点提醒我")
        assert is_schedule_intent("明天早上8点打开记事本")
        assert is_schedule_intent("每工作日9点打卡")
        assert is_schedule_intent("5分钟后提醒我")

    def test_weekday_patterns_detect_intent(self):
        assert is_schedule_intent("每星期一上午9点运行日报")
        assert is_schedule_intent("每周五晚上8点提醒我")

    def test_short_text_not_intent(self):
        assert is_schedule_intent("hi") is False

    def test_new_keywords_detect_intent(self):
        assert is_schedule_intent("每晚9点提醒我")
        assert is_schedule_intent("每早8点打开日报")
        assert is_schedule_intent("每朝7点跑步")
        assert is_schedule_intent("每夜11点关机")
        assert is_schedule_intent("每隔5分钟提醒我")
        assert is_schedule_intent("零点三十分执行备份")
        assert is_schedule_intent("零时整执行任务")

    def test_new_keywords_date_and_fuzzy(self):
        assert is_schedule_intent("每月1号提醒我")
        assert is_schedule_intent("9点左右提醒我")
        assert is_schedule_intent("每周末上午9点提醒我")


class TestParseTime:
    def test_tonight_9pm(self):
        r = parse("今晚9点提醒我写周报")
        assert r["confidence"] >= 65
        assert r["triggers"][0]["type"] == "cron"
        # 0 21 6 5 * => hour=21
        assert r["triggers"][0]["cron_expr"].startswith("0 21")

    def test_tonight_11pm(self):
        r = parse("今晚11点提醒我睡觉")
        assert r["confidence"] >= 60
        assert r["triggers"][0]["type"] == "cron"
        assert r["triggers"][0]["cron_expr"].startswith("0 23")

    def test_tomorrow_afternoon_5_30(self):
        r = parse("明天下午5点半提醒我开会")
        assert r["confidence"] >= 60
        assert r["triggers"][0]["type"] == "cron"
        assert r["triggers"][0]["cron_expr"].startswith("30 17")

    def test_every_weekday_9am(self):
        r = parse("每工作日上午9点打开企业微信")
        assert r["confidence"] >= 60
        assert r["triggers"][0]["type"] == "cron"
        assert r["triggers"][0]["cron_expr"] == "0 9 * * 1-5"

    def test_every_monday_9am(self):
        r = parse("每星期一上午9点运行日报")
        assert r["confidence"] >= 60
        assert r["triggers"][0]["cron_expr"].endswith("* * 1")

    def test_5_minutes_later(self):
        r = parse("5分钟后提醒我检查邮件")
        assert r["triggers"][0]["type"] == "interval"
        assert r["triggers"][0]["interval_seconds"] == 300

    def test_30_seconds_later(self):
        r = parse("30秒后提醒我")
        assert r["triggers"][0]["type"] == "interval"
        assert r["triggers"][0]["interval_seconds"] == 30

    # ── 新增: Chinese numeral normalization ──

    def test_chinese_numeral_11pm(self):
        """今晚十一点半 → 23:30"""
        r = parse("今晚十一点半提醒我睡觉")
        assert r["triggers"][0]["cron_expr"].startswith("30 23")

    def test_chinese_numeral_5_30pm(self):
        """明天下午五点半 → 17:30"""
        r = parse("明天下午五点半提醒我下班")
        assert r["triggers"][0]["cron_expr"].startswith("30 17")

    def test_chinese_numeral_10am_tuesday(self):
        """每星期二上午十点 → 10:00 每星期二"""
        r = parse("每星期二上午十点提醒我开会")
        assert r["triggers"][0]["cron_expr"] == "0 10 * * 2"

    def test_chinese_numeral_9_30am_friday(self):
        """每星期五上午九点半 → 9:30 每星期五"""
        r = parse("每星期五上午九点半提醒我开会")
        assert r["triggers"][0]["cron_expr"] == "30 9 * * 5"

    def test_chinese_compound_minutes_0_30(self):
        """零点三十分 → 0:30 (compound tens 三十)"""
        r = parse("零点三十分执行备份")
        assert r["triggers"][0]["cron_expr"].startswith("30 0")

    def test_chinese_compound_minutes_0_35(self):
        """零点三十五分 → 0:35"""
        r = parse("零点三十五分提醒我")
        assert r["triggers"][0]["cron_expr"].startswith("35 0")

    def test_chinese_compound_minutes_0_20(self):
        """零点二十分 → 0:20"""
        r = parse("零点二十分提醒我")
        assert r["triggers"][0]["cron_expr"].startswith("20 0")

    def test_chinese_compound_minutes_0_15(self):
        """零点十五分 → 0:15"""
        r = parse("零点十五分提醒我")
        assert r["triggers"][0]["cron_expr"].startswith("15 0")

    # ── 新增: 每晚/每早/每朝 每日变体 ──

    def test_every_night_9pm(self):
        """每晚9点 → 21:00 every day"""
        r = parse("每晚9点提醒我吃药")
        assert r["triggers"][0]["cron_expr"] == "0 21 * * *"

    def test_every_morning_8am(self):
        """每早8点 → 8:00 every day"""
        r = parse("每早8点打开日报")
        assert r["triggers"][0]["cron_expr"] == "0 8 * * *"

    def test_every_night_midnight(self):
        """每晚12点 → 0:00 (midnight, not noon)"""
        r = parse("每晚12点提醒我")
        assert r["triggers"][0]["cron_expr"] == "0 0 * * *"

    def test_every_morning_8_30(self):
        """每天早上8点半 → 8:30 every day"""
        r = parse("每天早上8点半提醒我")
        assert r["triggers"][0]["cron_expr"] == "30 8 * * *"

    # ── 新增: 每N小时 ──

    def test_every_n_hours(self):
        """每3小时 → */3 cron"""
        r = parse("每3小时提醒我喝水")
        assert r["triggers"][0]["cron_expr"] == "0 */3 * * *"

    # ── 新增: 每半小时 ──

    def test_every_half_hour(self):
        """每半小时 → 1800s interval"""
        r = parse("每半小时提醒我")
        assert r["triggers"][0]["type"] == "interval"
        assert r["triggers"][0]["interval_seconds"] == 1800

    def test_every_half_hour_variant(self):
        """每半个小时后提醒我 → 1800s interval"""
        r = parse("每半个小时后提醒我")
        assert r["triggers"][0]["type"] == "interval"
        assert r["triggers"][0]["interval_seconds"] == 1800

    # ── 新增: 相对时间变体 ──

    def test_half_minute_later(self):
        """半分钟后 → 30s interval"""
        r = parse("半分钟后提醒我")
        assert r["triggers"][0]["type"] == "interval"
        assert r["triggers"][0]["interval_seconds"] == 30

    def test_n_hours_later(self):
        """2小时后 → 7200s interval"""
        r = parse("2小时后提醒我")
        assert r["triggers"][0]["type"] == "interval"
        assert r["triggers"][0]["interval_seconds"] == 7200

    def test_mixed_hours_minutes_later(self):
        """1小时30分后 → 5400s interval"""
        r = parse("1小时30分后提醒我")
        assert r["triggers"][0]["type"] == "interval"
        assert r["triggers"][0]["interval_seconds"] == 5400

    def test_every_n_minutes_interval(self):
        """每隔5分钟 → 300s interval"""
        r = parse("每隔5分钟提醒我")
        assert r["triggers"][0]["type"] == "interval"
        assert r["triggers"][0]["interval_seconds"] == 300

    def test_every_n_seconds_interval(self):
        """每隔10秒 → 10s interval"""
        r = parse("每隔10秒执行任务")
        assert r["triggers"][0]["type"] == "interval"
        assert r["triggers"][0]["interval_seconds"] == 10

    # ── 新增: 大后天 ──

    def test_day_after_tomorrow_3pm(self):
        """大后天下午3点 → 15:00 in 3 days"""
        r = parse("大后天下午3点提醒我")
        assert r["triggers"][0]["type"] == "cron"
        assert r["triggers"][0]["cron_expr"].startswith("0 15")

    def test_day_after_tomorrow_11am(self):
        """大後天上午11点 → 11:00 in 3 days"""
        r = parse("大後天上午11点提醒我")
        assert r["triggers"][0]["cron_expr"].startswith("0 11")

    # ── 新增: 半 suffix in weekday/workday ──

    def test_weekday_half(self):
        """每星期一上午9点半 → 9:30"""
        r = parse("每星期一上午9点半提醒我")
        assert r["triggers"][0]["cron_expr"] == "30 9 * * 1"

    def test_workday_half(self):
        """每工作日上午8点半 → 8:30 工作日"""
        r = parse("每工作日上午8点半提醒我")
        assert r["triggers"][0]["cron_expr"] == "30 8 * * 1-5"

    # ── 新增: 每周末 ──

    def test_every_weekend_9am(self):
        """每周末上午9点 → 0 9 * * 0,6"""
        r = parse("每周末上午9点打开企业微信")
        assert r["triggers"][0]["cron_expr"] == "0 9 * * 0,6"

    def test_every_weekend_2pm_with_minutes(self):
        """每周末下午2点半 → 30 14 * * 0,6"""
        r = parse("每周末下午2点半运行备份")
        assert r["triggers"][0]["cron_expr"] == "30 14 * * 0,6"

    # ── 新增: 每月X号 ──

    def test_monthly_1st_10am(self):
        """每月1号上午10点 → 0 10 1 * *"""
        r = parse("每月1号上午10点打开日报")
        assert r["triggers"][0]["cron_expr"] == "0 10 1 * *"

    def test_monthly_15th_2pm_with_half(self):
        """每月15号下午2点半 → 30 14 15 * *"""
        r = parse("每月15号下午2点半运行备份")
        assert r["triggers"][0]["cron_expr"] == "30 14 15 * *"

    def test_monthly_1st_chinese_numeral(self):
        """每月一号上午8点 → 0 8 1 * *  (中文数字 号)"""
        r = parse("每月一号上午8点提醒我吃药")
        assert r["triggers"][0]["cron_expr"] == "0 8 1 * *"

    def test_monthly_10th_chinese_numeral(self):
        """每月十号上午9点 → 0 9 10 * * (十号→10号)"""
        r = parse("每月十号上午9点提醒我吃药")
        assert r["triggers"][0]["cron_expr"] == "0 9 10 * *"

    def test_monthly_11th_chinese_numeral(self):
        """每月十一号上午9点 → 0 9 11 * * (十一号→11号)"""
        r = parse("每月十一号上午9点提醒我吃药")
        assert r["triggers"][0]["cron_expr"] == "0 9 11 * *"

    # ── 新增: 下周一/本周五 ──

    def test_next_monday_9am(self):
        """下周一上午9点 → 特定日期 9:00"""
        r = parse("下周一上午9点提醒我开会")
        assert r["triggers"][0]["type"] == "cron"
        assert r["triggers"][0]["cron_expr"].startswith("0 9")

    def test_this_friday_5pm(self):
        """本周五下午5点 → 特定日期 17:00"""
        r = parse("本周五下午5点提醒我开会")
        assert r["triggers"][0]["type"] == "cron"
        assert r["triggers"][0]["cron_expr"].startswith("0 17")

    def test_next_tuesday_chinese_10am(self):
        """下周二上午十点 → 特定日期 10:00"""
        r = parse("下周二上午十点提醒我开会")
        assert r["triggers"][0]["cron_expr"].startswith("0 10")

    def test_next_wednesday_half(self):
        """下周三上午9点半 → 特定日期 9:30"""
        r = parse("下周三上午9点半提醒我吃药")
        assert r["triggers"][0]["cron_expr"].startswith("30 9")

    # ── 新增: 模糊时间 ──

    def test_fuzzy_zuoyou(self):
        """9点左右 → 0 9 * * *, fuzzy flag"""
        r = parse("9点左右提醒我吃药")
        assert r["triggers"][0]["cron_expr"] == "0 9 * * *"
        assert r.get("fuzzy") is True

    def test_fuzzy_duo(self):
        """9点多 → 0 9 * * *, fuzzy flag"""
        r = parse("9点多提醒我吃药")
        assert r["triggers"][0]["cron_expr"] == "0 9 * * *"
        assert r.get("fuzzy") is True

    def test_fuzzy_with_period(self):
        """下午3点左右 → 0 15 * * *"""
        r = parse("下午3点左右提醒我吃药")
        assert r["triggers"][0]["cron_expr"] == "0 15 * * *"

    def test_fuzzy_gangguo(self):
        """9点刚过 → 0 9 * * *"""
        r = parse("9点刚过提醒我吃药")
        assert r["triggers"][0]["cron_expr"] == "0 9 * * *"


class TestParseActions:
    def test_notify_message(self):
        r = parse("今晚9点提醒我写周报")
        assert r.get("notify_message") == "写周报"

    def test_launch_target(self):
        r = parse("今晚9点打开企业微信")
        assert r.get("launch_target") == "企业微信"

    def test_notify_and_launch(self):
        r = parse("今晚9点提醒我写周报并打开企业微信")
        assert r.get("notify_message") == "写周报"
        assert r.get("launch_target") == "企业微信"
        assert r.get("name") == "写周报"

    def test_template_id(self):
        r = parse("每星期一上午9点运行日报")
        assert r.get("template_id") == "日报"

    def test_mixed_actions(self):
        r = parse("明早8点打开记事本并提醒我写日记")
        assert r.get("launch_target") == "记事本"
        assert r.get("notify_message") == "写日记"


class TestRiskLevel:
    def test_info_level_notify_only(self):
        assert parse("今晚9点提醒我喝水").get("risk_level") == "info"

    def test_info_level_interval(self):
        assert parse("5分钟后提醒我").get("risk_level") == "info"

    def test_notify_level_launch(self):
        assert parse("每工作日上午9点打开企业微信").get("risk_level") == "notify"

    def test_notify_level_run(self):
        assert parse("每晚9点运行备份脚本").get("risk_level") == "notify"

    def test_confirm_level_shutdown(self):
        assert parse("明天下午3点关机").get("risk_level") == "confirm"

    def test_confirm_level_reboot(self):
        assert parse("今晚12点重启电脑").get("risk_level") == "confirm"

    def test_critical_level_delete(self):
        assert parse("每星期一上午9点删除临时文件").get("risk_level") == "critical"

    def test_critical_level_format(self):
        assert parse("大后天下午2点格式化磁盘").get("risk_level") == "critical"

    def test_notify_beats_info(self):
        """提醒+打开 → notify (有启动动作)"""
        assert parse("提醒我吃饭并打开浏览器").get("risk_level") == "notify"
