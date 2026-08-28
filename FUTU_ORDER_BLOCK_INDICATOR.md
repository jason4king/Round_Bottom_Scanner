# 富途牛牛订单块主图指标

## 推荐：麦语言版本

请优先使用项目根目录的 `futu_order_block_indicator.myl`，将其全部复制到指标编辑器的“麦语言”页面。麦语言的 `VALUEWHEN` 和 `BARSLAST` 可以在突破发生时固定订单块边界，更接近网页图表中的水平矩形效果。

Python 页面在部分富途客户端没有开放 `value_when()`、`bars_last()` 和 `iff()`；项目中的 Python 文件因此只能提供滚动窗口近似效果，不能等价复刻固定订单块。

## 麦语言主版本

该版本把项目中的订单块算法压缩为“最近一个多头订单块 + 最近一个空头订单块”。富途公式无法确认支持 Python 版本所用的多对象生命周期，因此本版使用 `STICKLINE` 色带模拟区域。

## 富途设置

- 指标缩写：`OBZONE`
- 指标名称：`Order Block Zone`
- 指标对象：主图
- 无需额外创建参数；源码内的 `NN:=5` 是摆动确认半径，需要时可直接修改

## 公式源码

```text
{ Order Block Zone - Futu compatible edition }
{ NN is the confirmed swing radius; recommended: 5 }

NN:=5;

HCND:=REF(H,NN)=HHV(H,2*NN+1);
LCND:=REF(L,NN)=LLV(L,2*NN+1);

SH:=VALUEWHEN(HCND,REF(H,NN));
SL:=VALUEWHEN(LCND,REF(L,NN));

UPBRK:=CROSS(C,SH);
DNBRK:=CROSS(SL,C);

UPLEN:=BARSLAST(HCND)+1;
DNLEN:=BARSLAST(LCND)+1;

UPIDX:=LLVBARS(L,UPLEN);
DNIDX:=HHVBARS(H,DNLEN);

BULLTOP0:=REF(H,UPIDX);
BULLBOT0:=REF(L,UPIDX);
BEARTOP0:=REF(H,DNIDX);
BEARBOT0:=REF(L,DNIDX);

BULLTOP:=VALUEWHEN(UPBRK,BULLTOP0);
BULLBOT:=VALUEWHEN(UPBRK,BULLBOT0);
BEARTOP:=VALUEWHEN(DNBRK,BEARTOP0);
BEARBOT:=VALUEWHEN(DNBRK,BEARBOT0);

BULLAGE:=BARSLAST(UPBRK);
BEARAGE:=BARSLAST(DNBRK);

BULLON:=COUNT(L<BULLBOT,BULLAGE+1)=0;
BEARON:=COUNT(H>BEARTOP,BEARAGE+1)=0;

STICKLINE(BULLON,BULLTOP,BULLBOT,8,0),COLORBLUE;
STICKLINE(BEARON,BEARTOP,BEARBOT,8,0),COLORRED;
```

## 逻辑对应

- `PH` / `PL`：等待右侧 `N` 根 K 线后确认摆动高低点，不回填未来信号。
- `UPBRK`：收盘向上突破最近确认的摆动高点。
- `DNBRK`：收盘向下突破最近确认的摆动低点。
- 多头订单块：向上突破前，从摆动高点至突破区间内最低 K 线的高低范围。
- 空头订单块：向下突破前，从摆动低点至突破区间内最高 K 线的高低范围。
- 多头区跌破下沿后失效；空头区突破上沿后失效。

## 已知限制

1. 只保留最近一组多头和空头区域；项目中的网页版本可同时维护多组区域。
2. 富途不同客户端版本的函数库可能不同。如果编译器提示不支持 `VALUEWHEN`、`LLVBARS`、`HHVBARS` 或动态周期参数，需要根据客户端函数库改写。
3. `.ftindex` 必须由富途客户端在公式测试通过后导出，本项目不能直接生成可靠的富途专有导入文件。
4. 订单块是结构分析区域，不是独立买卖信号。

## 导入步骤

打开富途桌面端：`报价 → 日K → 指标管理 → 新建指标`，选择主图，添加参数 `N`，粘贴公式后依次点击“测试指标”和“应用”。测试通过后可从指标管理中导出 `.ftindex`。
