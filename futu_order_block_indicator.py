# 富途牛牛 Python 自定义主图指标
# 复制本文件全部内容到：指标编辑 -> Python

indicator(
    "OBZONE",
    "订单块区域",
    True,
    "突破确认摆动高低点后生成最近的多空订单块；区域被价格破坏后停止显示。",
)

pivot_radius = input_parameter("摆动确认半径", 5)
source_lookback = input_parameter("订单块来源回看", 20)
zone_lifetime = input_parameter("区域最长显示周期", 60)
bull_color = input_parameter("多头订单块颜色", Color.rgb(43, 112, 190, 72))
bear_color = input_parameter("空头订单块颜色", Color.rgb(210, 73, 90, 72))


def order_blocks():
    h = high()
    l = low()
    c = close()

    # 富途部分客户端没有开放 iff/value_when/bars_last。
    # 本版本只使用 Sequence 运算和方法，按有限窗口计算，不引入未来数据。
    structure_window = 2 * pivot_radius + 1
    prior_high = ref(h.hhv(structure_window), 1)
    prior_low = ref(l.llv(structure_window), 1)

    # 收盘首次突破此前结构窗口的高点或低点。
    up_break = (c > prior_high) & (ref(c, 1) <= ref(prior_high, 1))
    down_break = (c < prior_low) & (ref(c, 1) >= ref(prior_low, 1))

    # 用突破前窗口极值和平均K线振幅估算订单块范围。
    # 这种写法不依赖“条件发生时赋值”函数，兼容精简版富途编译器。
    average_range = ref((h - l).sma(source_lookback), 1)
    bull_bottom = ref(l, 1).llv(source_lookback)
    bull_top = bull_bottom + average_range
    bear_top = ref(h, 1).hhv(source_lookback)
    bear_bottom = bear_top - average_range

    # 最近 zone_lifetime 根K线发生过突破时显示；价格破坏外沿时隐藏。
    bull_recent = (up_break * 1).sum(zone_lifetime) > 0
    bear_recent = (down_break * 1).sum(zone_lifetime) > 0
    bull_active = bull_recent & (l >= bull_bottom)
    bear_active = bear_recent & (h <= bear_top)

    return bull_top, bull_bottom, bull_active, bear_top, bear_bottom, bear_active


if __name__ == "__main__":
    bull_top, bull_bottom, bull_active, bear_top, bear_bottom, bear_active = order_blocks()

    plot_fillcolor("多头订单块", bull_top, bull_bottom, bull_active, bull_color)
    plot_fillcolor("空头订单块", bear_top, bear_bottom, bear_active, bear_color)

    # 暴露给指标回测/量化条件使用：1 表示当前存在有效区域。
    output_parameter(
        bullish_order_block=bull_active,
        bearish_order_block=bear_active,
    )
