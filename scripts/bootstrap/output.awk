# bootstrap 的逐行日志接收器；只依赖系统 awk（包括 macOS awk）。
# EVENT 来自本仓库的生产者，其他内容默认是诊断正文。
# 调用方使用 LC_ALL=C；将终端覆盖操作转成独立记录，EOF 自动补换行。
BEGIN {
    log_path = ENVIRON["DOTFILES_LOG"]
    task = ENVIRON["DOTFILES_TASK_LOG"]
    failure = ENVIRON["DOTFILES_TASK_FAILURE"]
}
function utf8_prefix(line, limit) {
    # 上限按字节计，但不能截在 UTF-8 续字节之前；日志正文不经此裁剪。
    while (limit > 0 && substr(line, limit + 1, 1) ~ /^[\200-\277]$/) limit--
    return substr(line, 1, limit)
}
function publish(line,    visible, level) {
    visible = source == "entry"
    if (line ~ /^@@DOTFILES\/1 EVENT /) {
        line = substr(line, 20)
        visible = 1
        if (failure != "" && line ~ /^FAIL[: ]/) {
            print "reported" > failure
            if (fflush(failure)) exit 74
        }
    } else if (line ~ /^@@DOTFILES\/1 DETAIL /) {
        line = substr(line, 21)
    } else if (source == "native" || source == "deploy" || source == "legacy") {
        # 原生命令没有事件接口，保留其明确的警告/错误及后续 hint。
        # 未知格式失败由调用方显示末尾摘要，完整正文始终写入日志。
        level = line ~ /^(W:|E:|[Ww][Aa][Rr][Nn]([Ii][Nn][Gg])?[:!]|[Ee][Rr][Rr][Oo][Rr]:|[Ff][Aa][Tt][Aa][Ll]:|FAIL[: ]|hint:)/
        # 原生多行诊断的缩进正文属于同一条提醒，例如 Stow 的完整冲突列表。
        visible = level || (attention && line ~ /^[ \t]+[^ \t]/)
        attention = visible
    }
    print line >> log_path
    print line >> task
    if (fflush(log_path) || fflush(task)) exit 74
    if (stream == "1") {
        print "@@DOTFILES/1 " (visible ? "EVENT " : "DETAIL ") line
        if (fflush()) exit 74
    } else if (visible) {
        print utf8_prefix(line, 800)
        if (fflush()) exit 74
    }
}
{
    if (summary_limit) {
        print utf8_prefix($0, summary_limit)
        next
    }
    # CSI/OSC 状态跨记录保留；仅移除显示控制符，不删除诊断文字。
    for (i = 1; i <= length($0); i++) {
        c = substr($0, i, 1)
        if (escape == "osc") {
            if (c == "\007" || (previous == "\033" && c == "\\")) escape = ""
            previous = c
        } else if (escape == "csi") {
            if (c ~ /[@-~]/) escape = ""
        } else if (escape == "esc") {
            escape = c == "[" ? "csi" : (c == "]" ? "osc" : "")
        } else if (c == "\033") escape = "esc"
        else if (c == "\r" || c == "\b") {
            if (text != "") publish(text)
            text = ""
        } else if (c !~ /[[:cntrl:]]/ || c == "\t") text = text c
    }
    if (text != "" || $0 == "") publish(text)
    text = ""
}
