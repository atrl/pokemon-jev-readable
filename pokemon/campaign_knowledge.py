"""Source-labelled story objectives, never a controller or a button script.

The source lineage is pinned but is not byte-identical to the supported ROM.
It supplies hypotheses about destinations and interactions, not proof that a
story event happened. Only verified observation facts complete objectives.
Bare booleans are accepted for caller-normalized facts/offline fixtures;
production readers should always supply the evidence wrappers described below.
"""

from __future__ import annotations

from copy import deepcopy


SOURCE = {
    "repository": "Rangi42/redstarbluestar",
    "commit": "08deafad427f0904f285e515c360003efc19d3dc",
    "rom_sha1": "e2d564a5172d38e44b4f4b8f7d9db92f644cf5e9",
    "full_source_binary_match": False,
    "quality": "source_prior",
}

# Symbols here are documentation for the evidence reader. This module does not
# read RAM, interpret source addresses as verified, or promote event flag bits.
FACT_EVENT_SYMBOLS = {
    "oak_appeared_in_pallet": "EVENT_OAK_APPEARED_IN_PALLET",
    "followed_oak_into_lab": "EVENT_FOLLOWED_OAK_INTO_LAB",
    "oak_asked_to_choose_mon": "EVENT_OAK_ASKED_TO_CHOOSE_MON",
    "starter_received": "EVENT_GOT_STARTER",
    "rival_lab_battled": "EVENT_BATTLED_RIVAL_IN_OAKS_LAB",
    "oak_parcel_received": "EVENT_GOT_OAKS_PARCEL",
    "parcel_delivered": "EVENT_OAK_GOT_PARCEL",
    "pokedex_received": "EVENT_GOT_POKEDEX",
    "ss_ticket_received": "EVENT_GOT_SS_TICKET",
    "cut_received": "EVENT_GOT_HM01",
    "surf_received": "EVENT_GOT_HM03",
    "strength_received": "EVENT_GOT_HM04",
    "fuji_rescued": "EVENT_RESCUED_MR_FUJI",
    "poke_flute_received": "EVENT_GOT_POKE_FLUTE",
    "route12_snorlax_cleared": "EVENT_BEAT_ROUTE12_SNORLAX",
    "silph_liberated": "EVENT_BEAT_SILPH_CO_GIOVANNI",
    "elite4_lorelei_defeated": "EVENT_BEAT_LORELEIS_ROOM_TRAINER_0",
    "elite4_bruno_defeated": "EVENT_BEAT_BRUNOS_ROOM_TRAINER_0",
    "elite4_agatha_defeated": "EVENT_BEAT_AGATHAS_ROOM_TRAINER_0",
    "elite4_lance_defeated": "EVENT_BEAT_LANCE",
    "champion_defeated": "EVENT_BEAT_CHAMPION_RIVAL",
}
ITEM_FACT_IDS = {
    "oak_parcel_received": 0x46,
    "ss_ticket_received": 0x3F,
    "lift_key_received": 0x4A,
    "silph_scope_received": 0x48,
    "poke_flute_received": 0x49,
    "gold_teeth_received": 0x40,
    "card_key_received": 0x30,
    "secret_key_received": 0x2B,
    "cut_received": 0xC4,
    "surf_received": 0xC6,
    "strength_received": 0xC7,
}
BADGES = ("boulder", "cascade", "thunder", "rainbow", "soul", "marsh", "volcano", "earth")


def fact(key, op="eq", value=True):
    return {"fact": key, "op": op, "value": value}


def any_of(*keys):
    return {"any": [fact(key) if isinstance(key, str) else key for key in keys]}


def all_of(*keys):
    return {"all": [fact(key) if isinstance(key, str) else key for key in keys]}


def obj(sprite, text_id=None, x=None, y=None, **extra):
    result = {"kind": "object", "sprite": sprite, "quality": "source_prior", **extra}
    result.update(
        {
            key: value
            for key, value in (("text_id", text_id), ("x", x), ("y", y))
            if value is not None
        }
    )
    return result


def coordinate(x, y, **extra):
    return {"kind": "coordinate", "x": x, "y": y, "quality": "source_prior", **extra}


def definition(
    ident, intent, why, target_map_id, completion, knowledge, selectors, source_file, requires=()
):
    return {
        "id": ident,
        "intent": intent,
        "why": why,
        "target_map_id": target_map_id,
        "target_map": target_map_id,
        "completion": completion,
        "requires": list(requires),
        "knowledge": knowledge,
        "interaction_selectors": selectors,
        "source": {
            **SOURCE,
            "files": [source_file, "constants/event_constants.asm", "constants/map_constants.asm"],
        },
    }


_OPENING = any_of("starter_received", fact("party_count", "gte", 1), "pokedex_received")
_DEX = fact("pokedex_received")

CATALOG = [
    definition(
        "meet_oak",
        "开始冒险：到真新镇北边出口触发大木博士",
        "先触发博士接应，实验室的选择宝可梦事件才会启动。",
        0x00,
        any_of(
            "oak_appeared_in_pallet", "followed_oak_into_lab", "oak_asked_to_choose_mon", _OPENING
        ),
        [
            "从家中经楼梯、门与地图连接来到真新镇；根据实际场景处理标题和开场菜单。",
            "PalletTownScript0 在尚未跟随博士时检查玩家 y == 1。先走向镇北出口，不要先在实验室反复交谈。",
            "x=10 只是北向道路候选位置；触发条件仅限制 y，导航应选择实际可达的 y=1 格子。",
        ],
        [coordinate(10, 1, trigger={"y": 1})],
        "scripts/pallettown.asm",
    ),
    definition(
        "follow_oak",
        "跟随大木博士进入实验室并听完说明",
        "获得选择初始宝可梦的许可。",
        0x28,
        any_of("oak_asked_to_choose_mon", _OPENING),
        [
            "接应后的移动由游戏脚本控制；观察对话与控制恢复，不要把等待当作完成。",
            "选择许可由 EVENT_OAK_ASKED_TO_CHOOSE_MON 表示；博士对象 text_id=5 在实验室 (5,2)。",
        ],
        [obj("SPRITE_OAK", 5, 5, 2)],
        "scripts/oakslab.asm",
        ("meet_oak",),
    ),
    definition(
        "choose_starter",
        "选择并领取一只初始宝可梦",
        "建立能参与战斗的队伍。",
        0x28,
        _OPENING,
        [
            "实验室桌上三个精灵球对象位于 (6,3)、(7,3)、(8,3)，text_id 分别为 2、3、4。",
            "先确认实际可交互的对象与选择对话；按当前确认菜单作答，不使用预排按键序列。",
            "博士说明中的文本或一个球消失都不能替代已验证的队伍/领取事件。",
        ],
        [obj("SPRITE_BALL", 2, 6, 3), obj("SPRITE_BALL", 3, 7, 3), obj("SPRITE_BALL", 4, 8, 3)],
        "data/mapObjects/oakslab.asm",
        ("follow_oak",),
    ),
    definition(
        "lab_rival",
        "完成实验室内的首次劲敌战",
        "实验室剧情结束后才能开始送包裹。",
        0x28,
        any_of("rival_lab_battled", "oak_parcel_received", "parcel_delivered", _DEX),
        [
            "拿到初始宝可梦后，OaksLabScript10 在玩家 y == 6 时触发劲敌挑战。",
            "战斗由实时战斗菜单、HP、招式与结果决定；首次战斗标记表示已交战，不一定取胜。",
        ],
        [coordinate(5, 6, trigger={"y": 6})],
        "scripts/oakslab.asm",
        ("choose_starter",),
    ),
    definition(
        "collect_parcel",
        "到常青市商店领取大木博士的包裹",
        "这是取得图鉴的前置任务。",
        0x2A,
        any_of("oak_parcel_received", "parcel_delivered", _DEX),
        [
            "从真新镇沿 1 号道路到常青市商店；第一次进店会启动店员剧情。",
            "店员在 (0,5)，text_id=1；以实际剧情/包裹证据确认领取。",
        ],
        [obj("SPRITE_MART_GUY", 1, 0, 5)],
        "scripts/viridianmart.asm",
        ("lab_rival",),
    ),
    definition(
        "deliver_parcel",
        "回实验室把包裹交给大木博士",
        "推动博士发放图鉴。",
        0x28,
        any_of("parcel_delivered", _DEX),
        ["与实验室博士交谈；背包包裹消失本身不能证明已送达，应读取已验证交付事件。"],
        [obj("SPRITE_OAK", 5, 5, 2)],
        "scripts/oakslab.asm",
        ("collect_parcel",),
    ),
    definition(
        "receive_pokedex",
        "完成博士与劲敌对话，领取图鉴",
        "开启正式旅程。",
        0x28,
        _DEX,
        ["交付包裹后继续当前剧情；以 EVENT_GOT_POKEDEX 的已验证证据确认。"],
        [obj("SPRITE_OAK", 5, 5, 2)],
        "scripts/oakslab.asm",
        ("deliver_parcel",),
    ),
    definition(
        "boulder_badge",
        "穿过常青森林，挑战深灰道馆的小刚",
        "取得第一枚徽章并向月见山推进。",
        0x36,
        fact("badge_boulder"),
        ["通过真实地图连接前往深灰市；战前检查队伍 HP、招式 PP，必要时去宝可梦中心。"],
        [obj("SPRITE_BLACK_HAIR_BOY_2", 1, 4, 1)],
        "data/mapObjects/pewtergym.asm",
        ("receive_pokedex",),
    ),
    definition(
        "cascade_badge",
        "穿过月见山到华蓝市，挑战小霞",
        "瀑布徽章允许队伍在野外使用居合斩。",
        0x41,
        fact("badge_cascade"),
        [
            "月见山出口有化石剧情；选择可见对象并确认真实战斗和领取结果。",
            "不要把胜利文本当作徽章已入账；以已验证徽章位确认。",
        ],
        [obj("SPRITE_BRUNETTE_GIRL", 1, 4, 2)],
        "data/mapObjects/ceruleangym.asm",
        ("boulder_badge",),
    ),
    definition(
        "bill_ticket",
        "帮助 25 号道路的正辉并领取船票",
        "圣安奴号船票是登船获取居合斩的条件。",
        0x58,
        fact("ss_ticket_received"),
        [
            "先帮助宝可梦外形的正辉，再按照可见提示操作传送装置并与人形正辉交谈。",
            "同一位置可能有剧情前后不同的对象；以当前可见对象为准。",
        ],
        [obj("SPRITE_SLOWBRO", 1, 6, 5), obj("SPRITE_BILL", 2, 4, 4), obj("SPRITE_BILL", 3, 6, 5)],
        "scripts/billshouse.asm",
        ("cascade_badge",),
    ),
    definition(
        "receive_cut",
        "登上圣安奴号，帮助船长领取 HM01",
        "居合斩用于通过树木障碍。",
        0x65,
        fact("cut_received"),
        [
            "从华蓝向南，经地下通道到枯叶市；在船上处理劲敌战后与船长交谈。",
            "拿到 HM 不等于队伍已学会技能；后续须从实际菜单教学。",
        ],
        [obj("SPRITE_SS_CAPTAIN", 1, 4, 2)],
        "scripts/ssanne7.asm",
        ("bill_ticket",),
    ),
    definition(
        "prepare_cut",
        "让合适的队伍成员学会居合斩",
        "拥有 HM01 且队伍实际学会 CUT 才能切树。",
        0x05,
        all_of("badge_cascade", fact("party_move_ids", "contains", 15)),
        [
            "从背包选择 HM01 并查看兼容宝可梦；由模型根据真实菜单选择，避免覆盖需要保留的招式。",
            "源码 TryCut 同时检查队伍 CUT 和瀑布徽章。",
        ],
        [],
        "engine/overworld/field_moves.asm",
        ("receive_cut",),
    ),
    definition(
        "thunder_badge",
        "解开枯叶道馆机关并挑战马志士",
        "取得雷电徽章。",
        0x5C,
        fact("badge_thunder"),
        ["切开入口树木；垃圾桶开关会改变，不能使用固定坐标按键宏。根据实际文本记忆开关结果。"],
        [obj("SPRITE_ROCKER", 1, 5, 1)],
        "data/mapObjects/vermiliongym.asm",
        ("prepare_cut",),
    ),
    definition(
        "rainbow_badge",
        "经岩山隧道与紫苑镇前往玉虹，挑战莉佳",
        "取得彩虹徽章，为后续主线与怪力通行做准备。",
        0x86,
        fact("badge_rainbow"),
        ["按已观察地图连接找出口；黑暗区域可考虑取得并教学闪光，但闪光不是完成本目标的证据。"],
        [obj("SPRITE_ERIKA", 1, 4, 3)],
        "data/mapObjects/celadongym.asm",
        ("thunder_badge",),
    ),
    definition(
        "rocket_lift_key",
        "探索游戏厅火箭队基地，取得电梯钥匙",
        "打开通往基地首领区域的电梯。",
        0xCA,
        fact("lift_key_received"),
        [
            "游戏厅海报后有入口；击败持钥匙的火箭队员并实际拾取钥匙。",
            "击败队员、掉落钥匙、背包中已有钥匙是不同证据。",
        ],
        [obj("SPRITE_ROCKET", 4, 11, 2), obj("SPRITE_BALL", 9, 10, 2)],
        "data/mapObjects/rockethideout4.asm",
        ("rainbow_badge",),
    ),
    definition(
        "silph_scope",
        "击败基地坂木并拾取西尔佛检视镜",
        "识别宝可梦塔的幽灵。",
        0xCA,
        fact("silph_scope_received"),
        ["检视镜是首领身后的物品对象；战胜首领不等于已经拾取。"],
        [obj("SPRITE_GIOVANNI", 1, 25, 3), obj("SPRITE_BALL", 8, 25, 2)],
        "data/mapObjects/rockethideout4.asm",
        ("rocket_lift_key",),
    ),
    definition(
        "rescue_fuji",
        "登上宝可梦塔顶，救出富士老人",
        "获得宝可梦之笛的前置剧情。",
        0x94,
        any_of("fuji_rescued", "poke_flute_received"),
        ["携带检视镜识别塔内幽灵，按真实战斗推进并与顶层老人交谈。"],
        [obj("SPRITE_MR_FUJI", 4, 10, 3)],
        "scripts/pokemontower7.asm",
        ("silph_scope",),
    ),
    definition(
        "poke_flute",
        "在紫苑镇富士老人家领取宝可梦之笛",
        "用于唤醒挡路的卡比兽。",
        0x95,
        fact("poke_flute_received"),
        ["救援后的老人位于家中 (3,1)；确认已验证笛子事件或背包物品。"],
        [obj("SPRITE_MR_FUJI", 5, 3, 1)],
        "data/mapObjects/lavenderhouse1.asm",
        ("rescue_fuji",),
    ),
    definition(
        "open_fuchsia_route",
        "用笛子唤醒 12 号道路卡比兽，打开南行道路",
        "沿 12 至 15 号道路前往浅红市。",
        0x17,
        fact("route12_snorlax_cleared"),
        ["在卡比兽附近使用实际背包中的笛子；捕获或战斗结果必须由已验证剧情证据确认。"],
        [obj("SPRITE_SNORLAX", 1, 10, 62)],
        "scripts/route12.asm",
        ("poke_flute",),
    ),
    definition(
        "receive_surf",
        "到狩猎地带秘密小屋领取 HM03",
        "取得冲浪秘传机。",
        0xDE,
        fact("surf_received"),
        ["狩猎区有入场与步数约束；按实际地图和计数探索秘密小屋，背包满时先腾出空间。"],
        [obj("SPRITE_FISHER", 1, 3, 3)],
        "scripts/safarizonesecrethouse.asm",
        ("open_fuchsia_route",),
    ),
    definition(
        "gold_teeth",
        "在狩猎地带西区找到金假牙",
        "交还园长以获取怪力。",
        0xDB,
        any_of("gold_teeth_received", "strength_received"),
        ["西区金假牙物品对象位于 (19,7)，text_id=4；实际拾取后验证。"],
        [obj("SPRITE_BALL", 4, 19, 7)],
        "data/mapObjects/safarizonewest.asm",
        ("receive_surf",),
    ),
    definition(
        "receive_strength",
        "把金假牙交还浅红市园长，领取 HM04",
        "怪力用于冠军之路等巨石机关。",
        0x9B,
        fact("strength_received"),
        ["与园长交谈；EVENT_GAVE_GOLD_TEETH 与 EVENT_GOT_HM04 分别表示交付和获得秘传机。"],
        [obj("SPRITE_WARDEN", 1, 2, 3)],
        "scripts/fuchsiahouse2.asm",
        ("gold_teeth",),
    ),
    definition(
        "soul_badge",
        "挑战浅红道馆的阿桔",
        "浅红徽章允许野外冲浪。",
        0x9D,
        fact("badge_soul"),
        ["道馆有不可直接看见的墙；根据实际碰撞与成功移动更新通路。"],
        [obj("SPRITE_BLACKBELT", 1, 4, 10)],
        "data/mapObjects/fuchsiagym.asm",
        ("receive_strength",),
    ),
    definition(
        "prepare_surf",
        "让队伍学会冲浪并确认野外使用条件",
        "前往红莲岛和冠军之路需要水上通行。",
        0x07,
        all_of("badge_soul", fact("party_move_ids", "contains", 57)),
        ["HM03 教学须根据真实背包/队伍菜单；源码 TrySurf 同时要求队伍 SURF 和浅红徽章。"],
        [],
        "engine/overworld/field_moves.asm",
        ("soul_badge",),
    ),
    definition(
        "silph_card_key",
        "进入金黄市西尔佛大楼，拾取钥匙卡",
        "打开大楼锁门，通往首领区域。",
        0xD2,
        fact("card_key_received"),
        [
            "金黄关卡守卫需要饮料；可从玉虹百货购买并观察交付成功后通行。",
            "五层钥匙卡在 (21,16)，text_id=8；按实际传送板与地图连接导航。",
        ],
        [obj("SPRITE_BALL", 8, 21, 16)],
        "data/mapObjects/silphco5.asm",
        ("prepare_surf",),
    ),
    definition(
        "liberate_silph",
        "在西尔佛公司击败坂木",
        "解除金黄市火箭队主线阻碍。",
        0xEB,
        fact("silph_liberated"),
        ["通过锁门与传送板找到首领；准备与劲敌及坂木战斗。"],
        [obj("SPRITE_GIOVANNI", 3, 6, 9)],
        "scripts/silphco11.asm",
        ("silph_card_key",),
    ),
    definition(
        "marsh_badge",
        "通过金黄道馆传送板，挑战娜姿",
        "取得金色徽章。",
        0xB2,
        fact("badge_marsh"),
        ["记录每个传送板实际出口，不假设同一房间内所有传送板目的地相同。"],
        [obj("SPRITE_GIRL", 1, 9, 8)],
        "data/mapObjects/saffrongym.asm",
        ("liberate_silph",),
    ),
    definition(
        "secret_key",
        "冲浪到红莲岛，在宝可梦屋寻找秘密钥匙",
        "秘密钥匙用于进入红莲道馆。",
        0xD8,
        fact("secret_key_received"),
        ["使用雕像开关与楼层落点探索；地下层钥匙在 (5,13)，text_id=8。"],
        [obj("SPRITE_BALL", 8, 5, 13)],
        "data/mapObjects/mansion4.asm",
        ("marsh_badge",),
    ),
    definition(
        "volcano_badge",
        "进入红莲道馆并挑战夏伯",
        "取得深红徽章。",
        0xA6,
        fact("badge_volcano"),
        ["根据真实问答文本或战斗结果通过房间；不要把机关开启当作获得徽章。"],
        [obj("SPRITE_MR_MASTERBALL", 1, 3, 3)],
        "data/mapObjects/cinnabargym.asm",
        ("secret_key",),
    ),
    definition(
        "earth_badge",
        "返回常青道馆，挑战最后的馆主坂木",
        "取得第八枚绿色徽章，满足联盟徽章检查。",
        0x2D,
        fact("badge_earth"),
        ["准备好队伍并处理地板移动机关；八枚徽章要按已验证徽章位分别确认。"],
        [obj("SPRITE_GIOVANNI", 1, 2, 1)],
        "data/mapObjects/viridiangym.asm",
        ("volcano_badge",),
    ),
    definition(
        "prepare_strength",
        "让队伍学会怪力，准备冠军之路",
        "冠军之路的巨石机关需要怪力。",
        0x6C,
        all_of("badge_rainbow", fact("party_move_ids", "contains", 70)),
        ["获取 HM04 后教学怪力；实际巨石位置必须根据当前地图读取，不能重放固定推石脚本。"],
        [],
        "scripts/victoryroad1.asm",
        ("earth_badge",),
    ),
    definition(
        "league_lorelei",
        "通过冠军之路，挑战四天王科拿",
        "开始联盟挑战。",
        0xF5,
        fact("elite4_lorelei_defeated"),
        ["23 号道路检查徽章；进入联盟前回复队伍并准备补给。失败后先确认挑战状态是否重置。"],
        [obj("SPRITE_LORELEI", 1, 5, 2)],
        "scripts/lorelei.asm",
        ("prepare_strength",),
    ),
    definition(
        "league_bruno",
        "击败四天王希巴",
        "继续当前联盟挑战。",
        0xF6,
        fact("elite4_bruno_defeated"),
        ["用当前 HP、招式 PP 和敌方信息决定战斗；房间到达不等于胜利。"],
        [obj("SPRITE_BRUNO", 1, 5, 2)],
        "scripts/bruno.asm",
        ("league_lorelei",),
    ),
    definition(
        "league_agatha",
        "击败四天王菊子",
        "继续当前联盟挑战。",
        0xF7,
        fact("elite4_agatha_defeated"),
        ["确认战斗结果后再推进下一房间。"],
        [obj("SPRITE_AGATHA", 1, 5, 2)],
        "scripts/agatha.asm",
        ("league_bruno",),
    ),
    definition(
        "league_lance",
        "击败四天王阿渡",
        "打开通往冠军战的道路。",
        0x71,
        fact("elite4_lance_defeated"),
        ["EVENT_BEAT_LANCE 需要适配器验证；不能只根据房间名字宣告获胜。"],
        [obj("SPRITE_LANCE", 1, 6, 1)],
        "scripts/lance.asm",
        ("league_agatha",),
    ),
    definition(
        "champion",
        "战胜现任冠军劲敌",
        "赢得主线最终战。",
        0x78,
        fact("champion_defeated"),
        ["读取已验证的冠军战结果；战斗开始、看到冠军或进入房间都不足以证明胜利。"],
        [obj("SPRITE_BLUE", 1, 4, 2)],
        "scripts/gary.asm",
        ("league_lance",),
    ),
    definition(
        "hall_of_fame",
        "跟随博士进入名人堂并完成登记",
        "主线终点需要真实名人堂证据。",
        0x76,
        fact("hall_of_fame_entered"),
        [
            "名人堂脚本会保存记录并清除本轮四天王与冠军事件位。保存已验证的名人堂证据，不能因事件清零抹去通关。",
            "单凭地图编号或源码中存在名人堂不能确认通关。",
        ],
        [obj("SPRITE_OAK", 1)],
        "scripts/halloffameroom.asm",
        ("champion",),
    ),
]

for _index, _entry in enumerate(CATALOG):
    _entry["priority"] = len(CATALOG) - _index


def _verified_value(record):
    if type(record) is bool:  # explicit normalized caller/fixture contract
        return record
    if not isinstance(record, dict) or record.get("verified") is False:
        return None
    quality = record.get("quality", "")
    if "unverified" in str(quality) or str(quality).endswith("prior"):
        return None
    if (
        record.get("verified") is True
        or quality == "verified"
        or str(quality).startswith("verified_")
    ):
        return record.get("value")
    return None


def _facts(facts):
    facts = facts if isinstance(facts, dict) else {}
    nested = facts.get("facts")
    result = dict(nested if isinstance(nested, dict) else facts)
    badge_bits = _verified_value(result.get("badge_bits"))
    if type(badge_bits) is int and 0 <= badge_bits <= 255:
        for bit, name in enumerate(BADGES):
            result.setdefault(
                f"badge_{name}",
                {
                    "value": bool(badge_bits & (1 << bit)),
                    "verified": True,
                    "quality": "verified_badge_bits",
                    "source": "badge_bits",
                },
            )
    return result


def evaluate(predicate, facts):
    """Return True / False / None. None means missing or unverified evidence."""
    if "any" in predicate or "all" in predicate:
        mode = "any" if "any" in predicate else "all"
        results = [evaluate(child, facts) for child in predicate[mode]]
        if mode == "any":
            if any(value is True for value in results):
                return True
            return None if None in results else False
        if any(value is False for value in results):
            return False
        return None if None in results else True
    value = _verified_value(facts.get(predicate.get("fact")))
    if value is None:
        return None
    expected = predicate.get("value", True)
    op = predicate.get("op", "eq")
    if op == "eq":
        return type(value) is type(expected) and value == expected
    if op == "gte":
        return value >= expected if type(value) is int and type(expected) is int else None
    if op == "contains":
        return expected in value if isinstance(value, (list, tuple)) else None
    raise ValueError(f"Unknown completion predicate operator: {op}")


def _predicate_keys(predicate):
    if "fact" in predicate:
        return {predicate["fact"]}
    return {
        key
        for child in predicate.get("all", predicate.get("any", []))
        for key in _predicate_keys(child)
    }


def _target(entry, world):
    selectors = deepcopy(entry["interaction_selectors"])
    if not selectors:
        return None
    world = world if isinstance(world, dict) else {}
    if world.get("map_id") == entry["target_map_id"]:
        for selector in selectors:
            if selector["kind"] != "object":
                continue
            for candidate in world.get("objects", []):
                if not isinstance(candidate, dict) or candidate.get("visible") is False:
                    continue
                if all(
                    candidate.get(key) == selector[key]
                    for key in ("sprite", "text_id")
                    if key in selector
                ):
                    # Object location is accepted for navigation with its own
                    # quality; it never completes the story predicate.
                    return {
                        **selector,
                        **{
                            key: candidate[key]
                            for key in ("x", "y", "quality", "source", "visible")
                            if key in candidate
                        },
                    }
    return selectors[0]


def objective_for(facts, world, history=None):
    """Select an evidence-gated intent, without selecting or executing inputs.

    ``facts`` is snapshot.milestones (flat evidence wrappers). ``history`` may
    contain similarly wrapped ``facts``/``milestones``; naked completed IDs do
    not prove anything. The caller can persist the returned verified evidence.
    ``world`` contains current map metadata and is only a navigation prior.
    """
    normalized = _facts(facts)
    history = history if isinstance(history, dict) else {}
    historic = _facts(history.get("facts", history.get("milestones", {})))
    for key, record in historic.items():
        if _verified_value(record) is True and (
            key == "hall_of_fame_entered" or _verified_value(normalized.get(key)) is None
        ):
            normalized[key] = record
    completed = [
        entry["id"] for entry in CATALOG if evaluate(entry["completion"], normalized) is True
    ]
    if _verified_value(normalized.get("hall_of_fame_entered")) is True:
        return {
            "id": "main_story_complete",
            "status": "completed",
            "completion": True,
            "intent": "已验证进入名人堂，主线目标完成",
            "why": "存在已验证的名人堂证据。",
            "target_map_id": None,
            "target": None,
            "priority": 0,
            "knowledge": ["名人堂保存会重置联盟事件，完成结论依赖保存的名人堂证据。"],
            "completion_evidence": {
                "hall_of_fame_entered": deepcopy(normalized["hall_of_fame_entered"])
            },
            "completed_ids": completed,
            "source": deepcopy(SOURCE),
        }
    for definition_entry in CATALOG:
        if definition_entry["id"] in completed:
            continue
        entry = deepcopy(definition_entry)
        predicate = entry.pop("completion")
        unresolved_dependencies = [
            dependency for dependency in entry["requires"] if dependency not in completed
        ]
        evidence_keys = sorted(_predicate_keys(predicate))
        entry.update(
            {
                "status": "needs_data" if unresolved_dependencies else "active",
                "completion": evaluate(predicate, normalized),
                "completion_predicate": predicate,
                "target": _target(entry, world),
                "completed_ids": completed,
                "unresolved_dependencies": unresolved_dependencies,
                "completion_evidence": {
                    key: deepcopy(normalized.get(key)) for key in evidence_keys
                },
                "unknown_facts": [
                    key for key in evidence_keys if _verified_value(normalized.get(key)) is None
                ],
            }
        )
        return entry
    raise AssertionError("Hall of Fame must remain the terminal objective")


def catalog():
    """Return an isolated serializable catalog for viewers and planners."""
    return deepcopy(CATALOG)
