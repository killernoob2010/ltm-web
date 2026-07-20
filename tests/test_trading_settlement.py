import pytest

from backend.app.trading_settlement import parse_settlement_statement


def statement_fixture(scope="20260529", *, fee_total="1.01", exercise=False):
    exercise_block = """
行权明细  Exercise Statement
|成交日期|投资单元|交易所|交易编码|品种|合约|投/保|买/卖|是否行权|行权数量|行权价格|行权金额|行权盈亏|行权手续费|资金账号|
|20260529|TEST001|大商所|TESTCODE|铁矿石期权|i2607-P-750|套保|买|期权放弃|1|750.000|75000.00|0.00|0.00|TEST001|
|共   1条|||||||||1||75000.00|0.00|0.00||
""" if exercise else ""
    return f"""
测试期货有限公司
制表时间 Creation Date：20260529
交易结算单(盯市) Settlement Statement(MTM)
客户号 Client ID： TEST001
日期 Date：{scope}

资金状况 Account Summary AccountID：TEST001 Currency：CNY
期初结存 Balance B/F：100000.00  基础保证金 Initial Margin：0.00
出 入 金 Deposit/Withdrawal：0.00  期末结存 Balance C/F：100008.99
平仓盈亏 Realized P/L：10.00  质押金 Pledge Amount：0.00
持仓盯市盈亏 MTM P/L：0.00  客户权益 Client Equity：100008.99
手 续 费 Commission：{fee_total}  保证金占用 Margin Occupied：1000.00
权利金收入 Premium Received：0.00
权利金支出 Premium Paid：0.00

成交记录 Transaction Record
|成交日期|投资单元|交易所|交易编码|品种|合约|买/卖|投/保|成交价|手数|成交额|开平|手续费|平仓盈亏|权利金收支|成交序号|资金账号|
|20260529|TEST001|大商所|TESTCODE|铁矿石|i2609|卖|套保|785.000|1|78500.00|开|1.01|10.00|0.00|100001|TEST001|
|共 1条|||||||||1|78500.00||1.01|10.00|0.00|||

{exercise_block}
平仓明细 Position Closed
|平仓日期|投资单元|交易所|交易编码|品种|合约|开仓日期|投/保|买/卖|手数|开仓价|昨结算|成交价|平仓盈亏|权利金收支|资金账号|
|20260529|TEST001|大商所|TESTCODE|铁矿石|i2609|20260528|套保|买|1|786.000|784.000|785.000|10.00|0.00|TEST001|
|共 1条|||||||||1||||10.00|0.00||

持仓明细 Positions Detail
|投资单元|交易所|交易编码|品种|合约|开仓日期|投/保|买/卖|持仓量|开仓价|昨结算|结算价|浮动盈亏|盯市盈亏|保证金|期权市值|资金账号|
|TEST001|大商所|TESTCODE|铁矿石|i2609|20260529|套保|卖|1|785.000|784.000|785.000|0.00|0.00|1000.00|0.00|TEST001|
|共 1条||||||||1||||0.00|0.00|1000.00|0.00||

持仓汇总 Positions
|投资单元|交易编码|品种|合约|买持|买开仓均价|卖持|卖开仓均价|昨结算|今结算|持仓盯市盈亏|保证金占用|投/保|多头期权市值|空头期权市值|资金账号|
|TEST001|TESTCODE|铁矿石|i2609|0|0.000|1|785.000|784.000|785.000|0.00|1000.00|套保|0.00|0.00|TEST001|
|共 1条||||0||1||||0.00|1000.00||0.00|0.00||
"""


def test_parse_daily_statement_detects_scope_and_sections():
    result = parse_settlement_statement(
        statement_fixture().encode("gb18030"), "daily.txt"
    )

    assert result["metadata"]["statement_type"] == "daily"
    assert result["metadata"]["range_start"] == "20260529"
    assert result["metadata"]["range_end"] == "20260529"
    assert result["metadata"]["account_code"] == "TEST001"
    assert result["counts"] == {
        "trade": 1,
        "close": 1,
        "exercise": 0,
        "position": 1,
    }
    assert result["trades"][0]["transaction_no"] == "100001"
    assert result["positions"][0]["snapshot_date"] == "20260529"
    assert result["positions"][0]["valuation_price"] == 785
    assert result["positions"][0]["valuation_status"] == "settlement_reference"


def test_parse_monthly_statement_detects_range_and_abandonment():
    result = parse_settlement_statement(
        statement_fixture("20260501-20260529", exercise=True).encode("gb18030"),
        "monthly.txt",
    )

    assert result["metadata"]["statement_type"] == "monthly"
    assert result["metadata"]["range_start"] == "20260501"
    assert result["metadata"]["range_end"] == "20260529"
    assert result["counts"]["exercise"] == 1
    assert result["exercises"][0]["event_type"] == "expiry_abandon"


@pytest.mark.parametrize(
    ("raw_type", "expected"),
    [
        ("期权放弃", "expiry_abandon"),
        ("期权执行", "exercise"),
        ("期权履约", "assignment"),
    ],
)
def test_option_lifecycle_event_types_are_normalized(raw_type, expected):
    content = statement_fixture(
        "20260601-20260630", exercise=True
    ).replace("期权放弃", raw_type)

    result = parse_settlement_statement(content.encode("gb18030"), "monthly.txt")

    assert result["exercises"][0]["event_type"] == expected
    assert result["exercises"][0]["event_type_raw"] == raw_type


def test_statement_totals_must_match_detail_rows():
    with pytest.raises(ValueError, match="手续费汇总不一致"):
        parse_settlement_statement(
            statement_fixture(fee_total="2.01").encode("gb18030"), "bad.txt"
        )


def test_statement_rejects_unknown_text():
    with pytest.raises(ValueError, match="无法识别"):
        parse_settlement_statement("普通文本".encode("gb18030"), "bad.txt")
