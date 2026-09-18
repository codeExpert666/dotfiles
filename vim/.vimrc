" 加载没有用户 vimrc 时 Vim 原本会使用的现代默认配置。
unlet! skip_defaults_vim
source $VIMRUNTIME/defaults.vim

" 个人目录约定：持久状态统一放在默认 XDG state 目录。
let s:vim_state_dir = expand('~/.local/state/vim')
call mkdir(s:vim_state_dir, 'p', 0700)

let &viminfofile = s:vim_state_dir . '/viminfo'
let g:netrw_home = s:vim_state_dir

unlet s:vim_state_dir

" 在现代默认值上增加的个人设置。
" 显示行号
set number

" 高亮搜索结果
set hlsearch

" 垂直分屏时新分屏在右侧打开
set splitright

" 在 Ghostty 等支持 xterm 光标形状指令的终端中，按编辑模式切换光标。
" 插入模式用闪烁竖线，退出插入模式恢复闪烁方块；SSH 会话同样适用。
" 数字与 q 之间的空格属于控制序列，不能删除。
if !has('gui_running') && &term =~# '^\%(xterm\|screen\|tmux\)'
  let &t_SI = "\<Esc>[5 q"
  let &t_EI = "\<Esc>[1 q"
endif

" 括号配对使用深色字符与柔和蓝色背景，在黑色终端背景上保持清晰醒目。
highlight MatchParen term=NONE cterm=NONE ctermfg=235 ctermbg=110 gui=NONE guifg=#24273a guibg=#8aadf4
