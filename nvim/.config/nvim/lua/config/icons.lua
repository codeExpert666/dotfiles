-- 图标唯一来源：界面使用 Nerd Fonts Material (nf-md-*)。
-- 纯数据，不加载插件；原始字形不含空格，间距由插件适配层决定。
local g = {
  apple = "󰀵", -- nf-md-apple
  microsoft = "󰍲", -- nf-md-microsoft
  steam = "󰓓", -- nf-md-steam
  twitter = "󰕄", -- nf-md-twitter
  percent = "󰏰", -- nf-md-percent
  error = "󰀨", -- 保留既有 Material diagnostics 字形
  warn = "󰀦", -- 保留既有 Material diagnostics 字形
  hint = "󰌵", -- 保留既有 Material diagnostics 字形
  info = "󰋼", -- 保留既有 Material diagnostics 字形
  success = "󰄬", -- nf-md-check
  close = "󰅖", -- nf-md-close
  question = "󰘥", -- nf-md-help_circle_outline
  file = "󰧮", -- nf-md-file_document_outline
  folder = "󰉋", -- nf-md-folder
  folder_open = "󰝰", -- nf-md-folder_open
  search = "󰍉", -- nf-md-magnify
  replace = "󰛔", -- nf-md-find_replace
  filter = "󰈳", -- nf-md-filter_outline
  edit = "󰏫", -- nf-md-pencil
  save = "󰆓", -- nf-md-content_save
  history = "󰋚", -- nf-md-history
  config = "󰢻", -- nf-md-cog_outline
  console = "󰆍", -- nf-md-console
  window = "󱂬", -- nf-md-dock_window
  format = "󰉢", -- nf-md-format_align_left
  code = "󰅴", -- nf-md-code_tags
  enabled = "󰔡", -- nf-md-toggle_switch
  disabled = "󰔢", -- nf-md-toggle_switch_off
  exit = "󰈆", -- nf-md-exit_to_app
  tab = "󰓩", -- nf-md-tab
  ui = "󰏘", -- nf-md-palette
  debug = "󰃤", -- nf-md-bug
  trace = "󰯔", -- nf-md-timeline_text_outline
  clock = "󰅐", -- nf-md-clock_outline
  event = "󱐋", -- nf-md-lightning_bolt
  package = "󰏖", -- nf-md-package_variant
  robot = "󱙺", -- nf-md-robot_outline
  book = "󰗚", -- nf-md-book_open_page_variant
  github = "󰊤", -- nf-md-github
  git = "󰊢", -- nf-md-git
  branch = "󰘬", -- nf-md-source_branch
  commit = "󰜘", -- nf-md-source_commit
  added = "󰝒", -- 保留既有 Material git 字形
  modified = "󱇧", -- 保留既有 Material git 字形
  removed = "󱪡", -- 保留既有 Material git 字形
  renamed = "󰪹", -- nf-md-file_move
  ignored = "󰘓", -- nf-md-file_hidden
  unmerged = "󰘭", -- nf-md-source_merge
  untracked = "󰡯", -- nf-md-file_question
  dots = "󰇘", -- nf-md-dots_horizontal
  circle = "󰧟", -- nf-md-circle_small
  circle_outline = "󰝦", -- nf-md-circle_outline
  circle_filled = "󰝥", -- nf-md-circle
  pin = "󰐃", -- nf-md-pin
  chevron_right = "󰅂", -- nf-md-chevron_right
  chevron_left = "󰅁", -- nf-md-chevron_left
  chevron_down = "󰅀", -- nf-md-chevron_down
  chevron_up = "󰅃", -- nf-md-chevron_up
  arrow_right = "󰁔", -- nf-md-arrow_right
  arrow_left = "󰁍", -- nf-md-arrow_left
  arrow_up = "󰁝", -- nf-md-arrow_up
  arrow_down = "󰁅", -- nf-md-arrow_down
  lua = "󰢱", -- nf-md-language_lua
  docker = "󰡨", -- nf-md-docker
  react = "󰜈", -- nf-md-react
  haskell = "󰲒", -- nf-md-language_haskell
  ethereum = "󰡪", -- nf-md-ethereum
  google = "󰊭", -- nf-md-google
  bash = "󱆃", -- nf-md-bash
  test = "󰙨", -- nf-md-test_tube
  eye = "󰛐", -- nf-md-eye_outline
  play = "󰐊", -- nf-md-play
  pause = "󰏤", -- nf-md-pause
  stop = "󰓛", -- nf-md-stop
  step_into = "󰆹", -- nf-md-debug_step_into
  step_out = "󰆸", -- nf-md-debug_step_out
  step_over = "󰆷", -- nf-md-debug_step_over
  step_back = "󰒮", -- nf-md-skip_previous
  restart = "󰜉", -- nf-md-restart
  disconnect = "󰌙", -- nf-md-lan_disconnect
  pending = "󰦖", -- nf-md-progress_clock
  connection = "󰀃", -- nf-md-access_point
  connection_off = "󱔑", -- nf-md-access_point_off
  breakpoint_rejected = "󰅚", -- nf-md-close_circle_outline
  logpoint = "󰧭", -- nf-md-text_box_outline
  stopped = "󰳟", -- nf-md-arrow_right_circle
  comment = "󰆉", -- nf-md-comment_text_outline
  diff = "󰢪", -- nf-md-file_compare
  sync = "󰓦", -- nf-md-sync
  keyboard = "󰌌", -- nf-md-keyboard
  neovim = "󰰪", -- nf-md-alpha_v_box_outline
  rocket = "󱓟", -- nf-md-rocket_launch_outline
  timer = "󰔛", -- nf-md-timer_outline
  flame = "󰈸", -- nf-md-fire
  link = "󰌹", -- nf-md-link_variant
  image = "󰥶", -- nf-md-image_outline
  email = "󰇰", -- nf-md-email_outline
  web = "󰖟", -- nf-md-web
  lock = "󰍁", -- nf-md-lock_outline
  lazy = "󰒲", -- nf-md-sleep
  project = "󰉕", -- nf-md-folder_multiple_outline
  file_cog = "󱁼", -- nf-md-file_cog_outline
  return_key = "󰌑", -- nf-md-keyboard_return
  kind_array = "󰅨", -- nf-md-code_array
  kind_class = "󰠱", -- nf-md-shape
  kind_constant = "󰇼", -- nf-md-equal
  kind_constructor = "󱌣", -- nf-md-hammer_wrench
  kind_enum = "󰉻", -- nf-md-format_list_numbered
  kind_field = "󰠵", -- nf-md-table_column
  kind_function = "󰊕", -- nf-md-function
  kind_interface = "󰌘", -- nf-md-lan_connect
  kind_key = "󰌋", -- nf-md-key_variant
  kind_keyword = "󰌆", -- nf-md-key
  kind_method = "󰡱", -- nf-md-function_variant
  kind_module = "󰕳", -- nf-md-view_module
  kind_namespace = "󰅩", -- nf-md-code_braces
  kind_null = "󰟢", -- nf-md-null
  kind_number = "󰎠", -- nf-md-numeric
  kind_object = "󰆧", -- nf-md-cube_outline
  kind_operator = "󰃬", -- nf-md-calculator
  kind_property = "󰘮", -- nf-md-tune
  kind_snippet = "󰆐", -- nf-md-content_cut
  kind_string = "󰅳", -- nf-md-code_string
  kind_struct = "󰕴", -- nf-md-view_quilt
  kind_text = "󰦨", -- nf-md-text
  kind_typeparameter = "󰬛", -- nf-md-alpha_t_box
  kind_unit = "󰑭", -- nf-md-ruler
  kind_value = "󰻾", -- nf-md-identifier
  kind_variable = "󰫧", -- nf-md-variable
  kind_control = "󱃖", -- nf-md-code_braces_box
  maven = "", -- nf-dev-maven
  spring = "", -- nf-dev-spring
}

g.skip = g.step_over
g.breakpoint = g.circle_filled
g.breakpoint_condition = g.question
g.kind_boolean = g.enabled
g.kind_color = g.ui
g.kind_enummember = g.kind_enum
g.kind_event = g.event
g.kind_file = g.file
g.kind_folder = g.folder
g.kind_package = g.package
g.kind_reference = g.link
g.kind_collapsed = g.chevron_right

local M = { glyphs = g }

-- 文件品牌例外；其他未覆盖文件继续使用 mini.icons 的内置文件库。
M.exceptions = { maven = "nf-dev-maven", spring = "nf-dev-spring", yaml = "nf-seti-yml", toml = "nf-custom-toml" }
M.diagnostics = { Error = g.error, Warn = g.warn, Hint = g.hint, Info = g.info }
M.git = { added = g.added, modified = g.modified, removed = g.removed }
M.kinds = {
  Array = g.kind_array,
  Boolean = g.kind_boolean,
  Class = g.kind_class,
  Color = g.kind_color,
  Constant = g.kind_constant,
  Constructor = g.kind_constructor,
  Enum = g.kind_enum,
  EnumMember = g.kind_enummember,
  Event = g.kind_event,
  Field = g.kind_field,
  File = g.kind_file,
  Folder = g.kind_folder,
  Function = g.kind_function,
  Interface = g.kind_interface,
  Key = g.kind_key,
  Keyword = g.kind_keyword,
  Method = g.kind_method,
  Module = g.kind_module,
  Namespace = g.kind_namespace,
  Null = g.kind_null,
  Number = g.kind_number,
  Object = g.kind_object,
  Operator = g.kind_operator,
  Package = g.kind_package,
  Property = g.kind_property,
  Reference = g.kind_reference,
  Snippet = g.kind_snippet,
  String = g.kind_string,
  Struct = g.kind_struct,
  Text = g.kind_text,
  TypeParameter = g.kind_typeparameter,
  Unit = g.kind_unit,
  Value = g.kind_value,
  Variable = g.kind_variable,
  Control = g.kind_control,
  Collapsed = g.kind_collapsed,
  Unknown = g.question,
  Copilot = g.robot,
  Codeium = g.robot,
  Supermaven = g.robot,
  TabNine = g.robot,
}

M.dashboard = {
  f = g.search,
  n = g.added,
  g = g.search,
  r = g.history,
  c = g.config,
  s = g.save,
  x = g.package,
  l = g.lazy,
  L = g.lazy,
  q = g.exit,
  p = g.project,
}

M.lazy = {
  cmd = g.console,
  config = g.config,
  debug = g.debug,
  event = g.event,
  favorite = g.pin,
  ft = g.file,
  init = g.config,
  import = g.package,
  keys = g.keyboard,
  lazy = g.lazy,
  loaded = g.success,
  not_loaded = g.circle_outline,
  plugin = g.package,
  runtime = g.neovim,
  require = g.lua,
  source = g.code,
  start = g.play,
  task = g.success,
}

M.files = {
  ["compose.yaml"] = { glyph = g.docker, hl = "MiniIconsBlue" },
  ["compose.yml"] = { glyph = g.docker, hl = "MiniIconsBlue" },
  ["docker-compose.yaml"] = { glyph = g.docker, hl = "MiniIconsBlue" },
  ["docker-compose.yml"] = { glyph = g.docker, hl = "MiniIconsBlue" },
  ["Dockerfile"] = { glyph = g.docker, hl = "MiniIconsBlue" },
  ["Containerfile"] = { glyph = g.docker, hl = "MiniIconsBlue" },
  [".dockerignore"] = { glyph = g.docker, hl = "MiniIconsBlue" },
  ["devcontainer.json"] = { glyph = g.docker, hl = "MiniIconsBlue" },
  [".devcontainer.json"] = { glyph = g.docker, hl = "MiniIconsBlue" },
  ["pom.xml"] = { glyph = g.maven, hl = "MiniIconsRed" },
  ["mvnw"] = { glyph = g.console, hl = "MiniIconsRed" },
  ["mvnw.cmd"] = { glyph = g.console, hl = "MiniIconsRed" },
  ["maven-wrapper.properties"] = { glyph = g.maven, hl = "MiniIconsRed" },
  ["AGENTS.md"] = { glyph = g.robot, hl = "MiniIconsPurple" },
  ["AGENTS.override.md"] = { glyph = g.robot, hl = "MiniIconsPurple" },
  ["CLAUDE.md"] = { glyph = g.robot, hl = "MiniIconsPurple" },
  [".env"] = { glyph = g.config, hl = "MiniIconsYellow" },
  ["README"] = { glyph = g.book, hl = "MiniIconsYellow" },
  ["README.md"] = { glyph = g.book, hl = "MiniIconsYellow" },
  ["README.txt"] = { glyph = g.book, hl = "MiniIconsYellow" },
  ["application.yml"] = { glyph = g.spring, hl = "MiniIconsGreen" },
  ["application.yaml"] = { glyph = g.spring, hl = "MiniIconsGreen" },
  ["application.properties"] = { glyph = g.spring, hl = "MiniIconsGreen" },
  ["bootstrap.yml"] = { glyph = g.spring, hl = "MiniIconsGreen" },
  ["bootstrap.yaml"] = { glyph = g.spring, hl = "MiniIconsGreen" },
  ["bootstrap.properties"] = { glyph = g.spring, hl = "MiniIconsGreen" },
}

-- Lua 模式只匹配 basename；映射到标准文件名以复用图标与高亮。
M.file_patterns = {
  { "^compose%..+%.ya?ml$", "compose.yaml" },
  { "^docker%-compose%..+%.ya?ml$", "compose.yaml" },
  { "^Dockerfile%..+$", "Dockerfile" },
  { "^Containerfile%..+$", "Containerfile" },
  { "^application%-.+%.ya?ml$", "application.yml" },
  { "^application%-.+%.properties$", "application.properties" },
  { "^bootstrap%-.+%.ya?ml$", "bootstrap.yml" },
  { "^bootstrap%-.+%.properties$", "bootstrap.properties" },
  { "^%.env%..+$", ".env" },
}

-- 上游状态栏函数没有独立 icon 选项；只转换渲染结果开头的装饰，不改正文。
M.statusline_replacements = {
  [""] = g.debug,
  [""] = g.clock,
  [""] = g.robot,
  [""] = g.robot,
  [""] = g.warn,
}
M.callouts = {
  note = g.info,
  info = g.info,
  tip = g.hint,
  hint = g.hint,
  important = g.info,
  warning = g.warn,
  attention = g.warn,
  caution = g.error,
  danger = g.error,
  error = g.error,
  success = g.success,
  check = g.success,
  done = g.success,
  failure = g.close,
  fail = g.close,
  missing = g.close,
  question = g.question,
  help = g.question,
  faq = g.question,
  todo = g.success,
  bug = g.debug,
}

return M
