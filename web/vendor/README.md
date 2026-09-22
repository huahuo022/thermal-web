# web/vendor

前端预览要“所见即所打”，就得在浏览器里真正把二维码和条码编码出来。这里放的是
第三方编码实现与导出的编码表，都是宽松许可（MIT），只用于**预览渲染**——
实际打印仍然走打印机的原生命令（`GS ( k` 二维码、`GS k` 条码）。

| 文件 | 来源 | 用途 |
|---|---|---|
| `qrcode.js` | [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) 1.4.4，MIT | 二维码矩阵（`qrcode()` / `addData()` / `make()` / `isDark()`） |
| `qrcode_UTF8.js` | 同上（官方 UTF-8 补丁） | 让 `addData()` 按 UTF-8 编码，与打印机收到的字节一致 |
| `LICENSE-qrcode-generator.txt` | 同上 | 许可证原文 |
| `barcode-tables.js` | 由 [python-barcode](https://github.com/WhyNotHugo/python-barcode) 0.16.1（MIT）的编码表导出 | CODE128 / CODE39 / EAN-13 / UPC-A / ITF 的图案表 |

条码的编码逻辑（校验位、码集 B、宽窄展开、HRI 位置）在 `web/app.js` 里实现，
这里只放纯数据表，避免手抄出错。
