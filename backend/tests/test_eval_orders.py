"""订单问答评测逻辑测试（判定函数，不调用外部 API）。"""

from eval_orders import check_answer


def test_exact_number_normalization():
    assert check_answer("17000.00", "exact", "订单的总金额是 17,000.00 元。")
    assert check_answer("17000.00", "exact", "总金额是17000元")
    assert not check_answer("17000.00", "exact", "总金额是 16000.00 元。")


def test_exact_date():
    assert check_answer(
        "2026-03-20 16:00:00",
        "exact",
        "该订单于 2026-03-20 16:00:00 完成。",
    )


def test_contains_all():
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
