"""Split-specific, human-authored surface forms for synthetic SOS messages.

These phrases are intentionally separate from the normalized benchmark labels.
Changes here require a phrase-bank version/hash change in generated records.
"""

from dataclasses import dataclass
from typing import Literal

SplitName = Literal["train", "dev", "test"]


@dataclass(frozen=True)
class TemplateFamily:
    id: str
    split: SplitName
    opener: str
    closer: str


_FRAMES: dict[SplitName, tuple[tuple[str, str], ...]] = {
    "train": (
        ("Tôi cập nhật tình hình tại đây", "Đó là thông tin tôi có lúc này"),
        ("Em báo lại tình hình bên này", "Tôi sẽ gửi thêm nếu có tin mới"),
        ("Tin tôi vừa nhận được", "Tôi ghi lại đúng như đã nghe"),
        ("Tôi gửi một cập nhật từ khu này", "Thông tin này mới được chuyển tới tôi"),
        ("Tình hình tôi đang thấy", "Tôi đang tiếp tục theo dõi"),
        ("Đây là lời nhắn từ khu vực đó", "Tôi chưa có thông tin nào khác"),
        ("Mọi người ơi, tôi báo tin", "Tôi vừa gửi lại nội dung này"),
        ("Tôi nhắn ngắn gọn tình hình", "Đó là tất cả tin tôi nhận được"),
        ("Tôi vừa được báo như sau", "Tôi sẽ cập nhật khi biết thêm"),
        ("Tin mới từ phía trong", "Tình hình vẫn đang được theo dõi"),
        ("Tôi báo lại điều vừa nghe", "Thông tin đến đây là hết"),
        ("Em chuyển tin từ khu này", "Tôi đang đợi thêm tin"),
        ("Tôi đang ghi nhận tình hình", "Đây là nội dung được báo"),
        ("Báo nhanh từ khu vực này", "Tôi sẽ bổ sung nếu có thay đổi"),
        ("Hiện tôi nhận được tin này", "Đó là cập nhật mới nhất"),
        ("Có cập nhật từ khu đó", "Tôi lưu lại để mọi người biết"),
        ("Tôi nói ngắn gọn hiện trạng", "Tôi đã ghi nhận lời nhắn"),
        ("Tôi gửi bản tin ngắn", "Tình hình theo lời báo là vậy"),
    ),
    "dev": (
        ("Mình nhắn lại điều vừa nghe", "Mình sẽ nói thêm khi có tin"),
        ("Từ phía trong vừa có cập nhật", "Đây là phần mình ghi nhận được"),
        ("Ở đây có tình hình mới", "Mình đang chờ thông tin tiếp"),
        ("Mình vừa nghe báo từ khu đó", "Tạm thời mình biết như vậy"),
        ("Có lời nhắn mới từ khu này", "Mình đã ghi lại nguyên ý"),
        ("Cho mình gửi tình hình hiện tại", "Nếu có thay đổi mình sẽ báo"),
    ),
    "test": (
        ("Tôi báo lại tin từ phía trong", "Đây là toàn bộ tin vừa có"),
        ("Lời nhắn mới được chuyển ra", "Tôi sẽ tiếp tục ghi nhận"),
        ("Đây là tin mới từ khu ven đường", "Tình hình mới nhất là vậy"),
        ("Tôi đang chuyển thông tin nhận được", "Tôi chờ thêm tin để bổ sung"),
        ("Tin từ khu đó vừa tới", "Tôi đã ghi rõ nội dung được báo"),
        ("Tôi gửi lại lời báo hiện tại", "Tôi sẽ theo dõi thêm diễn biến"),
    ),
}

FAMILIES: dict[SplitName, tuple[TemplateFamily, ...]] = {
    split: tuple(
        TemplateFamily(f"family_{split}_{index:02d}", split, opener, closer)
        for index, (opener, closer) in enumerate(frames, start=1)
    )
    for split, frames in _FRAMES.items()
}

CONFLICTING_LOCATIONS: dict[SplitName, tuple[str, str]] = {
    "train": ("ở đầu con ngõ", "ở cuối con ngõ"),
    "dev": ("ở đầu lối mòn", "ở cuối lối mòn"),
    "test": ("ở đầu đường đất", "ở cuối đường đất"),
}

# Each partition has its own lexical choices for every evidence-bearing cue.
PHRASES: dict[SplitName, dict[str, tuple[str, ...]]] = {
    "train": {
        "count_exact": ("có {n} người", "bên này {n} người", "chúng tôi có {n} người"),
        "count_zero": ("hiện không có ai ở khu được báo",),
        "count_range": ("từ {low} đến {high} người", "ít nhất {low} người"),
        "count_vague": ("có vài người", "một nhóm người đang ở đây"),
        "injury_yes": ("có người bị thương", "một người đang bị thương"),
        "injury_no": ("không ai bị thương", "hiện không có người bị thương"),
        "injury_hedged": ("hình như có người bị thương", "nghe nói có người bị thương"),
        "urgent": ("có người bất tỉnh", "một người đang khó thở"),
        "trapped": ("mọi người bị kẹt", "có người đang mắc kẹt"),
        "water": ("nước đang dâng", "nước lên nhanh ở đây"),
        "fire": ("có khói bốc lên", "khói vẫn bốc lên"),
        "structure": ("tường đã nứt", "có vết nứt trên nhà"),
        "access_blocked": ("xe không vào được", "đường cho xe đã bị chặn"),
        "access_passable": ("xe vẫn vào được", "đường cho xe hiện còn thông"),
        "boat": ("cần xuồng tới hỗ trợ", "xin một chiếc xuồng"),
        "medical": ("cần đội y tế", "xin người hỗ trợ y tế"),
        "evacuation": ("cần giúp đưa người ra", "xin hỗ trợ sơ tán"),
        "child": ("có trẻ nhỏ ở đây", "trong nhóm có em nhỏ"),
        "older_adult": ("có người lớn tuổi", "trong nhà có cụ lớn tuổi"),
        "limited_mobility": ("có người đi lại khó khăn", "có người không tự đi được"),
        "pregnant_person": ("có người đang mang thai",),
        "disability_reported": ("có người khuyết tật",),
        "vulnerable_none": ("không có ai thuộc nhóm cần hỗ trợ đặc biệt",),
        "vulnerable_hedged": ("hình như có trẻ nhỏ ở đó",),
        "location": ("gần cây cầu nhỏ", "bên cạnh sân chung", "ở đầu con ngõ"),
        "irrelevant": ("điện thoại tôi gần hết pin", "tôi đang dùng máy mượn"),
        "urgency": ("tin này cần được xem sớm",),
    },
    "dev": {
        "count_exact": ("nhóm ở đây gồm {n} người", "đếm được {n} người"),
        "count_zero": ("đã xác nhận không có ai tại khu này",),
        "count_range": ("khoảng {low} đến {high} người", "không dưới {low} người"),
        "count_vague": ("còn mấy người ở trong", "chưa rõ, chỉ biết có một nhóm người"),
        "injury_yes": ("một người có vết thương", "có người đang chảy máu nhẹ"),
        "injury_no": ("mọi người nói chưa ai bị thương", "hiện ai cũng không bị thương"),
        "injury_hedged": ("có lẽ một người bị thương", "không chắc nhưng nghe có người bị thương"),
        "urgent": ("một người không còn tỉnh", "có người đang khó thở"),
        "trapped": ("nhóm này chưa thoát ra được", "có người bị kẹt bên trong"),
        "water": ("mực nước đang cao thêm", "nước đang dâng vào khu này"),
        "fire": ("thấy khói dày gần đó", "khói đang bốc ra"),
        "structure": ("tường nhà bị nứt", "một phần tường bị nứt"),
        "access_blocked": ("lối xe chạy đã bị chắn", "xe hiện không qua nổi"),
        "access_passable": ("xe còn đi qua được", "lối xe chạy vẫn thông"),
        "boat": ("mong có thuyền đến đón",),
        "medical": ("mong đội y tế đến",),
        "evacuation": ("xin đưa mọi người ra ngoài",),
        "child": ("có em bé trong nhóm",),
        "older_adult": ("có người già đi cùng",),
        "limited_mobility": ("có người không tự di chuyển được",),
        "pregnant_person": ("có người đang mang thai ở đây",),
        "disability_reported": ("có người có khuyết tật trong nhóm",),
        "vulnerable_none": ("nhóm xác nhận không có người cần trợ giúp đặc biệt",),
        "vulnerable_hedged": ("nghe nói có em bé ở trong",),
        "location": ("gần bến đò cũ", "ở cạnh nhà sinh hoạt chung", "phía cuối lối mòn"),
        "irrelevant": ("pin máy tôi sắp cạn", "tôi đang gõ bằng điện thoại cũ"),
        "urgency": ("tin này nên được đọc sớm",),
    },
    "test": {
        "count_exact": ("ở điểm này còn {n} người", "báo lại là {n} người"),
        "count_zero": ("không có ai ở vị trí được báo lúc này",),
        "count_range": ("ước từ {low} tới {high} người", "đã thấy tối thiểu {low} người"),
        "count_vague": ("chỉ biết có vài người", "có mấy người nhưng chưa đếm được"),
        "injury_yes": ("đã báo có người bị thương do va đập", "có người mang vết thương"),
        "injury_no": ("người trong nhóm xác nhận không ai bị thương", "chưa có ai bị thương"),
        "injury_hedged": (
            "có thể có người bị thương",
            "người báo tin chưa chắc nhưng có người bị thương",
        ),
        "urgent": ("một người đã bất tỉnh", "có người thở rất khó"),
        "trapped": ("có người không ra được khỏi chỗ đó", "nhóm người đang bị kẹt lại"),
        "water": ("nước tiếp tục dâng quanh họ", "nước ngập thêm từng lúc"),
        "fire": ("có khói đi lên từ phía nhà", "khói đang bốc từ mái nhà"),
        "structure": ("tường nhà bị rạn nứt", "một góc tường đã nứt"),
        "access_blocked": ("ô tô không tới được", "đường xe vào đã bít"),
        "access_passable": ("ô tô còn tới được", "đường xe vào còn đi được"),
        "boat": ("xin xuồng tới",),
        "medical": ("xin đội y tế",),
        "evacuation": ("cần hỗ trợ đưa họ ra",),
        "child": ("trong đó có trẻ nhỏ",),
        "older_adult": ("có cụ già cùng nhóm",),
        "limited_mobility": ("có người khó tự đi",),
        "pregnant_person": ("trong nhóm có người mang thai",),
        "disability_reported": ("có người có khuyết tật ở đó",),
        "vulnerable_none": ("không người nào ở đó thuộc diện cần hỗ trợ riêng",),
        "vulnerable_hedged": ("có thể có trẻ nhỏ ở bên trong",),
        "location": ("bên cây cầu tre", "gần sân phơi chung", "ở đoạn cuối đường đất"),
        "irrelevant": ("tôi đang nhắn bằng máy của bạn", "màn hình điện thoại hơi tối"),
        "urgency": ("tin này cần được ghi nhận sớm",),
    },
}


def families_for(split: SplitName) -> tuple[TemplateFamily, ...]:
    return FAMILIES[split]
