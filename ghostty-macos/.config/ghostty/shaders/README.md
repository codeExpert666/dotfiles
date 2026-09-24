# Ghostty 光标 shader

本目录为 macOS Ghostty 提供淡出拖尾和涟漪，采用
[sahaj-b/ghostty-cursor-shaders 的 Customized 示例][demo]，并修复了 warp 的模糊边界处理。
它随 `ghostty-macos` 包部署到 `~/.config/ghostty/shaders`。

[demo]: https://github.com/sahaj-b/ghostty-cursor-shaders/blob/0a274beac8b93ee6ce6b94402b7313a0417b8e38/README.md#example-faded-warp--ripple

## 使用与验证

[platform.ghostty](../platform.ghostty) 按以下顺序加载；修改效果时应一起维护整个列表，重复配置项会按顺序叠加。

```ini
custom-shader = shaders/cursor_warp.glsl
custom-shader = shaders/ripple_cursor.glsl
custom-shader-animation = always
```

`always` 使失去焦点的终端继续渲染，让光标由竖线变为空心方块时的涟漪播放完毕。

保存后，在 Ghostty 中按 `Cmd + Shift + ,` 重载。移动光标观察拖尾，在 Vim 中切换插入模式与普通模式观察宽度变化触发的涟漪。
`+validate-config` 只能检查配置，shader 编译错误需查看 Ghostty 日志。

## 参数

当前参数沿用作者示例：

| 文件                 | 参数                            | 作者示例值                    |
| -------------------- | ------------------------------- | ----------------------------- |
| `cursor_warp.glsl`   | `DURATION`                      | `0.15`                        |
| `cursor_warp.glsl`   | `TRAIL_SIZE`                    | `0.8`                         |
| `cursor_warp.glsl`   | `THRESHOLD_MIN_DISTANCE`        | `1.0`                         |
| `cursor_warp.glsl`   | `BLUR`                          | `1.0`                         |
| `cursor_warp.glsl`   | `TRAIL_THICKNESS`               | `1.0`                         |
| `cursor_warp.glsl`   | `TRAIL_THICKNESS_X`             | `0.9`                         |
| `cursor_warp.glsl`   | `FADE_ENABLED`                  | `1.0`                         |
| `cursor_warp.glsl`   | `FADE_EXPONENT`                 | `5.0`                         |
| `ripple_cursor.glsl` | `DURATION`                      | `0.15`                        |
| `ripple_cursor.glsl` | `MAX_RADIUS`                    | `0.026`                       |
| `ripple_cursor.glsl` | `RING_THICKNESS`                | `0.02`                        |
| `ripple_cursor.glsl` | `CURSOR_WIDTH_CHANGE_THRESHOLD` | `0.5`                         |
| `ripple_cursor.glsl` | `COLOR`                         | `vec4(0.35, 0.36, 0.44, 0.8)` |
| `ripple_cursor.glsl` | `BLUR`                          | `3.5`                         |
| `ripple_cursor.glsl` | `ANIMATION_START_OFFSET`        | `0.01`                        |

## 来源与本地调整

上游提交为 `0a274beac8b93ee6ce6b94402b7313a0417b8e38`，更新时同步记录新提交与原始文件校验值：

| 上游原始文件                                                                                                                             | 上游 SHA-256                                                       |
| ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| [cursor_warp.glsl](https://github.com/sahaj-b/ghostty-cursor-shaders/blob/0a274beac8b93ee6ce6b94402b7313a0417b8e38/cursor_warp.glsl)     | `589f71f537cee890a07e408f13d062ff7d93de6d9f742c84e4bde6104c5d51fb` |
| [ripple_cursor.glsl](https://github.com/sahaj-b/ghostty-cursor-shaders/blob/0a274beac8b93ee6ce6b94402b7313a0417b8e38/ripple_cursor.glsl) | `410866d96782dc07c70cf31c58b8740cc22dba0e7817febeedf716dec4d8b884` |

warp 的水平、垂直移动分支更新外层 `effectiveBlur`，避免同名局部变量使关闭模糊失效；
模糊宽度为零时使用硬边界，避免向 `smoothstep` 传入相同的两端值。其余 GLSL 逻辑沿用对应上游示例。

[LICENSE](LICENSE) 原样保留该提交的 MIT 许可证。warp 距离函数中的 Inigo Quilez 署名及
[2D distance functions 文章](https://iquilezles.org/articles/distfunctions2d/)链接也予以保留；
注释未标明具体示例或版本，其复用范围和原始许可仍待向上游核实，不能据此推定文章所有示例均适用 MIT 许可。
