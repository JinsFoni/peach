"""Pornhub 人物页读成的形状：头像地址、简介、资料表与换算后的那一行。

页面片段取自站上真人页，只留下这几处用到的节点（正文、作品墙与脚本都删掉）。
"""
import unittest

from peach import pornhub_profiles as profiles
from peach.follow import FollowSourceError

AVATAR = ("https://ei.phncdn.com/pics/users/587/676/961/avatar1598785651/"
          "(m=eidYGCjadOf)(mh=W2VLPIc2a4WcnOlI)200x200.jpg")
BIO = "Love being here from time to time. I enjoyed my PornHub era ☕️"
#: 站上「简介」页签里那张表，按页面顺序。
INFO = (
    ("感情状态", "单身"), ("喜欢", "男女通吃"), ("性别", "女性"), ("出身地", "Czech Republic"),
    ("三围", "35-25-36"), ("身高", '5\' 6" (167cm)'), ("体重", "111lbs. (50kg)"),
    ("种族", "White"), ("发色", "Brunette"), ("假奶子", "否"), ("纹身", "No"),
    ("是否有穿环", "Yes"), ("兴趣爱好", "Boring AF."),
    ("会勾起我性欲的是", "When you watching me, fucking love it!"),
    ("会浇灭我性欲的是", "Dunno what can turn me off."),
    ("视频播放量", "561,849,851"), ("主页浏览量", "174,425,083"), ("已看视频", "707"),
)


def page(*, info=INFO, avatar=AVATAR, name="Mini Diva", bio=BIO) -> str:
    pieces = "".join(
        f'<div class="infoPiece"><span>{label}:</span>'
        f'<span itemprop="" class="smallInfo">{value}</span></div>' for label, value in info)
    heading = f'<h1 itemprop="name">{name}</h1>' if name else ""
    picture = (f'<div class="previewAvatarPicture"><img id="getAvatar" src="{avatar}"'
               ' class="jcrop-preview" alt="Mini Diva"></div>') if avatar else ""
    return f"""<html><head><meta charset="utf-8"></head><body>
<div id="avatarPicture">{picture}</div>
<div class="nameSubscribe"><div class="name">{heading}<span class="verifiedPornstar"></span></div></div>
<div class="infoBoxes">
  <div class="infoBox"><span class="big">266</span><div class="title">模特排名</div></div>
  <div class="infoBox videoViews" data-title="视频播放量: 561,849,851"><span class="big">561M</span></div>
  <div class="infoBox" data-title="订阅者: 1,430,676"><span class="big">1.4M</span></div>
</div>
<section class="aboutMeSection sectionDimensions"><div class="title">简介 Mini Diva</div>
  <div>{bio}</div></section>
<div class="detailedInfo column text js-infoText"><div class="content-columns js-highestChild columns-2">
{pieces}
</div></div>
<div class="detailedInfo column text socialsColumn js-socials"><ul class="socialList">
  <li><a href="https://onlyfans.com/minidivaonly/c1"><i class="ph-icon"></i>
    <span class="socialText">官方网址</span></a></li>
  <li><a href="https://twitter.com/minidivaonly"><i class="ph-icon-twitterX"></i>
    <span class="socialText">Twitter</span></a></li>
</ul></div>
</body></html>"""


def labels(parsed: dict) -> list[str]:
    return [item["label"] for item in parsed["about"]]


def shown(parsed: dict, label: str) -> str:
    return next(item["value"] for item in parsed["about"] if item["label"] == label)


class AddressTests(unittest.TestCase):
    def test_the_page_is_the_one_the_source_points_at_on_the_chinese_site(self):
        """首页、作品页、简介页给的是同一批节点，所以照来源自己的地址取，只换主机。"""
        for url in ("https://cn.pornhub.com/model/mini-diva/videos",
                    "https://www.pornhub.com/model/mini-diva/videos",
                    "https://pornhub.com/model/mini-diva/videos"):
            self.assertEqual(profiles.page_url(url), "https://cn.pornhub.com/model/mini-diva/videos")
        self.assertEqual(profiles.page_url("https://cn.pornhub.com/model/mini-diva"),
                         "https://cn.pornhub.com/model/mini-diva")
        self.assertEqual(profiles.page_url("https://cn.pornhub.com/pornstar/x/about"),
                         "https://cn.pornhub.com/pornstar/x/about")
        self.assertEqual(profiles.page_url("https://cn.pornhub.com/users/foo/videos?page=2"),
                         "https://cn.pornhub.com/users/foo/videos")

    def test_a_source_that_is_not_a_model_page_is_refused(self):
        """别家的地址、或者站上的汇总页都不是人物页，拼不出简介页就当场说清楚。"""
        for url in ("https://www.youtube.com/@someone", "https://cn.pornhub.com/",
                    "https://cn.pornhub.com/model/", "https://cn.pornhub.com/pornstar",
                    "https://cn.pornhub.com/view_video.php?viewkey=6798a78e9c631", ""):
            with self.assertRaises(FollowSourceError):
                profiles.page_url(url)


class PageTests(unittest.TestCase):
    def test_name_avatar_bio_and_socials_come_from_the_page(self):
        parsed = profiles.parse(page())
        self.assertEqual(parsed["name"], "Mini Diva")
        self.assertEqual(parsed["avatar_url"], AVATAR)
        self.assertEqual(parsed["bio"], BIO)
        self.assertEqual([item["label"] for item in parsed["socials"]], ["官方网址", "Twitter"])
        self.assertEqual(parsed["stats"], {"视频播放量": "561,849,851", "订阅者": "1,430,676"})

    def test_the_avatar_has_to_live_on_the_sites_own_image_host(self):
        """头像地址只能取自站自己的图床：别的域来的 src 一律当没给，账本里也就没有可取的图。"""
        parsed = profiles.parse(page(avatar="https://example.com/avatar.jpg"))
        self.assertEqual(parsed["avatar_url"], "")
        self.assertEqual(profiles.parse(page(avatar="")).get("avatar_url"), "")

    def test_a_page_without_a_name_is_treated_as_a_changed_page(self):
        for broken in ("<html></html>", page(name="", avatar="")):
            with self.assertRaises(FollowSourceError):
                profiles.parse(broken)

    def test_the_name_falls_back_to_the_avatars_alt_text(self):
        """标题节点不见了还有一处名字：头像那张图的 `alt` 就是她的名字。"""
        self.assertEqual(profiles.parse(page(name=""))["name"], "Mini Diva")

    def test_site_facts_are_listed_in_page_order_with_page_wording(self):
        """页面用语与顺序照站上，身高三围折进资料卡「身材」那一格，不再列一遍。"""
        parsed = profiles.parse(page())
        self.assertEqual(labels(parsed), [
            "感情", "取向", "性别", "出生地", "体重", "种族", "发色", "胸部", "纹身", "穿环",
            "兴趣", "勾起性欲", "浇灭性欲"])
        self.assertEqual(shown(parsed, "感情"), "单身")
        self.assertEqual(shown(parsed, "出生地"), "Czech Republic")
        self.assertEqual(shown(parsed, "体重"), "50 kg")
        self.assertEqual(parsed["about"][0], {"label": "感情", "value": "单身", "glyph": "heart"})

    def test_yes_and_no_become_the_wording_the_page_uses(self):
        parsed = profiles.parse(page())
        self.assertEqual(shown(parsed, "胸部"), "天然")
        self.assertEqual(shown(parsed, "纹身"), "无")
        self.assertEqual(shown(parsed, "穿环"), "有")
        implants = profiles.parse(page(info=(("假奶子", "Yes"), ("纹身", "是"))))
        self.assertEqual(shown(implants, "胸部"), "加工")
        self.assertEqual(shown(implants, "纹身"), "有")

    def test_a_fact_the_table_does_not_know_yet_is_still_listed(self):
        """站上新加的资料项照原文列：那是站上确有的说法，丢掉就少一项读数。"""
        parsed = profiles.parse(page(info=(("烟酒", "No"),)))
        self.assertEqual(parsed["about"], [{"label": "烟酒", "value": "No", "glyph": "info"}])

    def test_weight_falls_back_to_pounds_when_only_pounds_are_given(self):
        parsed = profiles.parse(page(info=(("体重", "111lbs."),)))
        self.assertEqual(shown(parsed, "体重"), "50 kg")


class RecordTests(unittest.TestCase):
    def test_height_and_measurements_are_stored_in_centimetres(self):
        row = profiles.record(profiles.parse(page()))
        self.assertEqual(row["height_cm"], 167)
        self.assertEqual((row["bust_cm"], row["waist_cm"], row["hip_cm"]), (89, 64, 91))
        self.assertEqual(row["birthplace"], "Czech Republic")
        self.assertEqual(row["hobbies"], "Boring AF.")
        self.assertEqual(row["tags"], [])

    def test_measurements_written_in_centimetres_are_taken_as_they_are(self):
        row = profiles.record(profiles.parse(page(info=(("三围", "90-63-91 cm"),))))
        self.assertEqual((row["bust_cm"], row["waist_cm"], row["hip_cm"]), (90, 63, 91))

    def test_the_original_wording_lands_in_one_json_column(self):
        """账本只有固定的那几列；站上其余的读数按原文收进 `raw`，页面用语留给展示那一层。"""
        row = profiles.record(profiles.parse(page()))
        self.assertEqual(row["raw"]["名前"], "Mini Diva")
        self.assertEqual(row["raw"]["简介"], BIO)
        self.assertEqual([item["label"] for item in row["raw"]["资料"]],
                         labels(profiles.parse(page())))

    def test_a_page_with_only_a_name_writes_only_the_name(self):
        """只有名字（没填资料表、没有简介）也是一条能落库的资料行，缺的列不写。"""
        row = profiles.record(profiles.parse(page(info=(), bio="")))
        self.assertEqual(row["raw"]["名前"], "Mini Diva")
        self.assertNotIn("height_cm", row)
        self.assertNotIn("birthplace", row)
        self.assertEqual(row["raw"]["资料"], [])


if __name__ == "__main__":
    unittest.main()
