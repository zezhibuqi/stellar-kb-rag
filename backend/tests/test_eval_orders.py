"""订单问答评测逻辑测试（判定函数，不调用外部 API）。"""

from eval_orders import check_answer


def test_exact_number_normalization():
    """exact 判定允许千分位/单位/空格差异，但数值必须一致（17000 ≠ 16000）。"""
    assert check_answer("17000.00", "exact", "订单的总金额是 17,000.00 元。")
    assert check_answer("17000.00", "exact", "总金额是17000元")
    assert not check_answer("17000.00", "exact", "总金额是 16000.00 元。")


def test_exact_date():
    """日期时间按原样匹配（评测关注「有没有答对完成时间」）。"""
    assert check_answer(
        "2026-03-20 16:00:00",
        "exact",
        "该订单于 2026-03-20 16:00:00 完成。",
    )


def test_contains_all():
    """contains_all 要求答案同时包含全部关键词（订单号 + 流程词），缺一即判错。"""
    assert check_answer(
        ["DD20260315004", "退换货"],
        "contains_all",
        "订单 DD20260315004 已完成；破损商品可申请退换货。",
    )
    assert not check_answer(
        ["DD20260315004", "退换货"],
        "contains_all",
        "订单 DD20260315004 已完成。",
    )
