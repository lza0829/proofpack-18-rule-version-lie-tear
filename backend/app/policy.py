"""全局互斥策略（单选，不做运行时开关）。

本系统在两条互斥命运中各只贯彻一种，且全站唯一：

1. 规则变更后，历史「清单通过」包的命运：
   ``invalidate_old_passes`` —— 规则变更使旧通过作废，必须按新规则复检通过后才可放行。
   （另一种「历史通过仍有效」不实现。）

2. 规则变更与在飞放行的冲突处置：
   ``reject_release_on_version_drift`` —— 放行写入时若发现规则版本已变，则拒绝该次放行，
   且不得写出通过留痕。
   （另一种「变更提交时若存在写入中的放行则拒绝变更」不实现。）
"""

from __future__ import annotations

# 唯一取值，供代码判定与留痕展示；不要做成双值开关。
POLICY_RULE_CHANGE_EFFECT = "invalidate_old_passes"
POLICY_INFLIGHT_CONFLICT = "reject_release_on_version_drift"

POLICY_LABELS = {
    "rule_change_effect": (
        "规则变更使历史「清单通过」作废，须按当前规则复检通过后才可放行"
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
