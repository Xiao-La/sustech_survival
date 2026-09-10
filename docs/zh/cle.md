# 语言指导（CLE / 语言中心语言指导服务）

在 e-hall 上预约**语言中心**老师的一对一指导：备考答疑、写作批改、口语练习、
出国文书修改。一次预约对应**一个 25 分钟时段**，地点在琳恩图书馆二楼203 ——
具体房间以时段返回的信息为准，不要凭印象写。

**认证：** `CleClient` 需要浏览器里引导出的 e-hall 会话（`EhallSession`）；
只用 CAS 票据直连时，应用的数据接口会返回 403。凭据链见 [SSO](sso.md)。

---

## 命令行

```bash
sustech cle semester              # 学期、服务开放窗口、教学周、额度
sustech cle types                 # 本学期开设的指导范围
sustech cle teachers              # 老师 × 指导范围 × 地点的资源列表
sustech cle buckets               # 固定的 25 分钟时段划分
sustech cle schedule --week 3     # 某一周的老师课表
sustech cle slots --days 5        # 空闲时段（已与实时占用做连接）
sustech cle walkin                # 当天剩余时段（现场预约，不占额度）
sustech cle mine --all            # 我的预约（含已取消 / 缺席）
sustech cle quota                 # 本学期已用 / 剩余次数
sustech cle policy                # 服务办法原文
sustech cle email --topic "雅思写作"      # 特别专项指导申请（仅邮件）
```

写操作默认只做预览，加 `--commit --yes` 才真正提交：

```bash
sustech cle book preview --slot <slot_id> --topic "雅思写作批改"
sustech cle book apply   --slot <slot_id> --topic "雅思写作批改" --commit --yes
sustech cle cancel preview --reservation <wid>
sustech cle cancel apply   --reservation <wid> --commit --yes
```

`--slot` 可以只给 `cle slots` 打印出的前几位；前缀不唯一时程序直接报错，
不会替你猜。取消所需的预约编号由 `cle mine` 打印。

---

## Python 接口

```python
from sustech_survival.ehall.cle import CleClient

c = CleClient()
sem = c.semester()                        # 学期、窗口、周次、额度
slots = c.slots(days=5, service="英语指导")
booked = c.book(slots[0], note="雅思写作批改")   # 默认只预览
```

读：`semester`、`service_types`、`teachers`、`time_buckets`、`schedule`、
`slots`、`walkin`、`my_reservations`、`quota`、`find_slot`、
`find_reservation`。写：`book`、`cancel`，两者的前置校验都在
`ehall/cle/reservation/`。

---

## 服务办法（程序会拦下来的几条）

- 每学期**最多 3 次**预约；现场预约（未预约直接去）不计入额度。
- **至少提前 1 天**预约；当天剩下的时段只能现场使用。
- **至少提前 2 天**取消；不足 2 天时系统会拒绝，并提示联系
  `cle@sustech.edu.cn`。
- 两次未取消的缺席，本学期余下时间无法再预约。
- **预约说明必填**，就是你的主题（备考、写作批改、文书修改）。书面材料
  篇幅在 500 字以内。
- **特别专项指导（周四 10:00 之后）只能邮件预约**，系统里约不到；
  `cle email` 会生成填好主题、姓名、学号的申请正文。

---

## 接口形态

CLE 应用的数据接口都在 `/dxggyw/sys/yyzxyy/` 下的 EMAP 模型上。预约提交的是
预约模型的**整套控件**（只发子集会被服务端以 `#E2140600091` 拒绝）；取消则是
「我的预约」路由上的**状态写入**，不是删除。字段级细节随模块走，见
`ehall/cle/reservation/wire.py` 的文档字符串（其中引用了抓取到的原始请求）。

---

## 相关

- [场地预约](booking.md) —— 校内场地。
- [图书馆预约](lib-booking.md) —— IC 图书馆讨论间。
- [教学信息服务](tis.md) —— 课表，用来找空档。
