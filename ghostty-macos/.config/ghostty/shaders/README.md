# Ghostty 光标 shader

当前启用 [sahaj-b/ghostty-cursor-shaders 的 Customized（faded warp + ripple）示例][demo]。
本目录只保留正在使用的两个 shader，参数沿用作者示例；
warp 另有下文记录的局部修复。

[demo]: https://github.com/sahaj-b/ghostty-cursor-shaders/blob/0a274beac8b93ee6ce6b94402b7313a0417b8e38/README.md#example-faded-warp--ripple

`platform.ghostty` 按顺序加载淡出拖尾和涟漪：

```ini
custom-shader = shaders/cursor_warp.glsl
custom-shader = shaders/ripple_cursor.glsl
custom-shader-animation = always
```

`always` 遵循上游关于失焦动画的说明：光标从竖线切为空心方块时，涟漪需要继续播放到结束，因此失去焦点的终端也会持续渲染。

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

warp 的本地修复：水平、垂直移动分支更新外层 `effectiveBlur`，避免同名局部变量使关闭模糊的逻辑失效；
模糊宽度为零时使用硬边界，避免向 `smoothstep` 传入相同的两端值。其余 GLSL 逻辑与对应上游示例一致，行尾空白已规范化。

- 上游提交：`0a274beac8b93ee6ce6b94402b7313a0417b8e38`
- 许可证：本目录的 `LICENSE` 同样原样取自该上游提交。

`cursor_warp.glsl` 中的距离函数还引用了 Inigo Quilez 的
[2D distance functions 文章](https://iquilezles.org/articles/distfunctions2d/)，
对应的作者署名和链接保留在代码中。该注释未标明具体示例或版本；其代码复用范围及
适用的原始许可声明尚待核实。本目录保留直接上游的 MIT 许可证，不据此推定该文章
所有示例的许可。后续应向上游确认具体来源，并按确认结果补充必要声明。

| 上游原始文件                                                                                                                             | 上游 SHA-256                                                       |
| ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| [cursor_warp.glsl](https://github.com/sahaj-b/ghostty-cursor-shaders/blob/0a274beac8b93ee6ce6b94402b7313a0417b8e38/cursor_warp.glsl)     | `589f71f537cee890a07e408f13d062ff7d93de6d9f742c84e4bde6104c5d51fb` |
| [ripple_cursor.glsl](https://github.com/sahaj-b/ghostty-cursor-shaders/blob/0a274beac8b93ee6ce6b94402b7313a0417b8e38/ripple_cursor.glsl) | `410866d96782dc07c70cf31c58b8740cc22dba0e7817febeedf716dec4d8b884` |

本目录属于 `ghostty-macos` Stow package，仅在 macOS 使用 `--no-folding` 部署到 `~/.config/ghostty/shaders`。
修改效果时维护 `platform.ghostty` 中的整个 `custom-shader` 列表；重复配置项会按顺序叠加。

保存配置后，在 Ghostty 中按 `Cmd + Shift + ,` 重载。移动光标可观察拖尾；涟漪由光标宽度变化触发，
例如在 Vim 中切换插入模式与普通模式。`+validate-config` 只能检查配置，shader 编译错误需要查看 Ghostty 日志。

更新上游文件时，同步更新本文件中的提交号和校验值。
