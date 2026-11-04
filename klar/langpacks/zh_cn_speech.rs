// 重译版 zh-CN 话术包（对照 en/speech.rs 原文 + 中文口语习惯）
// 原文件 src/lang/packs/zh_cn/speech.rs 首行标注 "// @generated ... DO NOT EDIT."
// —— 机翻痕迹的来源就是它。本文件为手工重译，保持结构/键名/顺序完全一致，可原地替换。
use crate::lang::speech::Speech;

pub(super) const SPEECH: Speech = Speech {
    // 原文 "I did not catch that."（原译把 unknown 和 need_on 两句挤成一句，还加了"比如说"）
    unknown: "没听清，再说一遍好吗？",
    need_on: "要打开什么？",
    need_off: "要关掉什么？",
    // 原文 "Tell me which device you mean."（原译错抄成"打开什么？"，丢了"哪个设备"）
    need_which: "你说的是哪个设备？",
    // 原文 "All right, I will drop the last sentence."
    correction: "好，刚才那句作废。",
    // 原文 "Do you mean {names}?"（原译只剩 "{names}?"，疑问句式全丢）
    clarify: "你是说{names}吗？",
    clarify_or: "还是",
    and_join: "和",
    // 原文 "{names} are on." / "{names} are off."（原译只剩名词+句号，谓语全丢）
    group_on: "{names}都打开了。",
    group_off: "{names}都关了。",
    turn_on: "{target}开了。",
    // 原文 "Started {target}."
    turn_on_scene: "{target}已启动。",
    turn_off: "{target}关了。",
    // 原文 "{target} is switched."
    toggle: "{target}已切换。",
    // 原文 "Opened {target}." / "Closed {target}."（原译只剩名词）
    cover_open: "{target}已打开。",
    cover_close: "{target}已关闭。",
    // 原文 "Locked {target}." / "Unlocked {target}."
    lock: "{target}已上锁。",
    unlock: "{target}已解锁。",
    // 原文 "{target} is at {n} percent."（原译 "{target} {n}%" 无谓语、百分号直出）
    light_set: "{target}已调到{n}%。",
    // 原文 "{target} is {color}."
    light_color: "{target}已设为{color}。",
    // 原文 "{noun} {target} is at {n} degrees."（原译语序照搬英文、无"度"）
    climate_set: "{target}{noun}已设为{n}度。",
    heat_noun: "暖气",
    cool_noun: "空调",
    // 原文 "The temperature {loc}."（{loc} 已含房间名，原译"温度{loc}"会出"温度客厅"）
    get_temp: "{loc}温度。",
    // 原文 "I am checking {target}."
    get_state: "正在查询{target}。",
    media_pause: "已暂停。",
    media_play: "继续播放。",
    media_next: "下一首。",
    media_previous: "上一首。",
    media_mute: "已静音。",
    media_unmute: "已取消静音。",
    // 原文 "Volume is set."
    media_volume: "音量已设置。",
    // 原文 "Starting music."（原译变成问句"播放什么？"，语义反了）
    media_search: "开始播放。",
    // 原文 "Moving music."（原译按完成时译"已转移"，实际是进行中）
    media_transfer: "正在转移播放。",
    media_favorite: "已加入收藏。",
    // 原文 "Fan is at {n} percent."
    fan_set: "风扇已调到{n}%。",
    vacuum_start: "{target}开始清扫。",
    vacuum_dock: "{target}正在回充。",
    vacuum_default: "扫地机",
    // 原文 "Timer is running."
    timer_start: "定时器已启动。",
    timer_cancel: "定时器已取消。",
    timer_pause: "定时器已暂停。",
    timer_how_long: "多长时间？",
    list_add: "已加入清单。",
    calendar_list: "{items}",
    // 原文 "No upcoming events."
    calendar_empty: "近期没有日程。",
    // 原文 "No calendar is available."
    calendar_none: "没有可用的日历。",
    // 原文 "{summary} {when}."（两个占位符硬拼，中文需加连接词）
    calendar_created: "已创建{summary}，{when}。",
    // 原文 "What should I call the event?"
    calendar_need_title: "这个日程叫什么？",
    // 原文 "When is the event?"
    calendar_need_when: "日程定在什么时候？",
    // 原文 "That calendar cannot be changed."（原译"日历只读"是术语直译）
    calendar_readonly: "这个日历不能修改。",
    // 原文 "Event deleted."
    calendar_deleted: "日程已删除。",
    // 原文 "{summary} {when}."
    calendar_moved: "{summary}已改到{when}。",
    calendar_which: "哪个日程？",
    // 原文 "That event has no identifier."
    calendar_no_uid: "这个日程没有标识。",
    // 原文 "No music player is available."
    no_music_player: "没有可用的播放器。",
    // 原文 "Done: {name}."（原译只剩 {name}，"完成"丢了）
    done: "完成：{name}。",
    light_suffix: "灯",
    area_light: "{loc}的灯",
    loc_in: "{room}",
    loc_in_der: "{room}",
    loc_home: "家里",
    or_home: "设备",
    room_names: &[("wohnzimmer", "客厅"), ("kuche", "厨房")],
    loc_der_rooms: &["厨房", "客厅"],
    // 原文各档性格前缀。原译问题：pirat 档 "Aye." 没译；hippie 档两句错位（All good→放松）
    personality: &[
        ("butler", &["好的。 ", "乐意效劳。 ", ""]),
        ("locker", &["行。 ", "搞定。 ", ""]),
        ("fuersorglich", &["这就去做。 ", "都安排好了。 ", ""]),
        ("party", &["走起。 ", "不错。 ", ""]),
        ("grantig", &["行吧。 ", "那就勉强一下。 ", ""]),
        ("sarkastisch", &["当然。 ", "真是意外呢。 ", ""]),
        ("pirat", &["遵命。 ", "船长。 ", ""]),
        ("hippie", &["都挺好。 ", "放轻松。 ", ""]),
        ("gollum", &["是。 ", "是的，我的宝贝。 ", ""]),
        ("jarvis", &["当然，先生。 ", "马上。 ", ""]),
    ],
    // 原文 "Should I really do that?"
    confirm: "确定要执行吗？",
};
