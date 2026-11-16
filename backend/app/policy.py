"""全局互斥策略（单选，不做运行时开关）。

本系统在两条互斥命运中各只贯彻一种，且全站唯一：

1. 规则变更后，历史「清单通过」包的命运：
   ``invalidate_old_passes`` —— 作废该包旧清单通过态与旧通过留痕，强制按当前启用项
   整表复检；复检全过前禁止放行。
   （另一种「历史通过仍有效」不实现。）

2. 规则变更与在飞放行的冲突处置：
   ``reject_release_on_version_drift`` —— 放行写入时若发现规则版本已变，则拒绝该次放行，
   且不得写出通过留痕。
   （另一种「变更提交时若存在写入中的放行则拒绝变更」不实现。）

不论哪条，以下不变量恒成立：
- 放行留痕中的规则版本必须与所绑清单结果的版本一致；
- 缺跑当前启用项不得放行；
- 变更与在飞放行不得共同留下「混版通过」。
"""

from __future__ import annotations

# 唯一取值，供代码判定与留痕展示；不要做成双值开关。
POLICY_RULE_CHANGE_EFFECT = "invalidate_old_passes"
POLICY_INFLIGHT_CONFLICT = "reject_release_on_version_drift"

# 「作废后待复检」是唯一的中间态：不可放行，直到一次绑定当前启用版本的复检
# 覆盖全部启用项并全过。旧通过态/旧通过留痕均在规则变更或放行被拒时作废。
STATUS_RECHECK = "recheck_required"
BLOCKING_PACK_STATUSES = (STATUS_RECHECK,)

POLICY_LABELS = {
    "rule_change_effect": (
        "规则变更作废旧清单通过态与旧通过留痕，须按当前启用项整表复检全过后才可放行"
    ),
    "inflight_conflict": (
        "放行写入时若发现规则版本已变，则拒绝该次放行且不写出通过"
    ),
}


def policy_snapshot() -> dict[str, str]:
    return {
        "rule_change_effect": POLICY_RULE_CHANGE_EFFECT,
        "rule_change_effect_label": POLICY_LABELS["rule_change_effect"],
        "inflight_conflict": POLICY_INFLIGHT_CONFLICT,
        "inflight_conflict_label": POLICY_LABELS["inflight_conflict"],
    }
