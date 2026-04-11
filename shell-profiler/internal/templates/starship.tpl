"$schema" = 'https://starship.rs/config-schema.json'

# Workspace profile: {{.ProfileName}} (template: {{.Template}})
# Accent color: {{.AccentColor}} | Glyph color: {{.GlyphColor}}
# Note: glyph color is ~10% darker than accent to compensate for font anti-aliasing
format = """
[ ](bg:color_orange)\
$os\
$custom\
$battery\
[](fg:{{.GlyphColor}} bg:#e5c750)\
$sudo\
$directory\
[](fg:#e5c750 bg:#007ee5)\
$kubernetes\
[](fg:#007ee5 bg:#8950e5)\
$terraform\
[](fg:#8950e5 bg:#d95252)\
$gcloud\
[](fg:#d95252 bg:#5d8d5f)\
$git_branch\
$git_status\
[](fg:#5d8d5f bg:#3e787a)\
$rust\
$golang\
$nodejs\
$python\
[](fg:#3e787a bg:#5c534c)\
$docker_context\
[ ](fg:#5c534c bg:#363230)\
$time\
[](fg:#363230)\
$line_break$character
"""

palette = 'gruvbox_dark'

[palettes.gruvbox_dark]
color_fg0 = '#fbf1c7'
color_fg1 = '#ffffff'
color_bg1 = '#3c3836'
color_bg3 = '#665c54'
color_blue = '#458588'
color_black = '#000000'
color_aqua = '#689d6a'
color_green = '#98971a'
color_orange = '{{.AccentColor}}'
color_purple = '#9959FF'
color_red = '#F15B5B'
color_yellow = '#FFDE59'
color_sharp_blue = '#008cff'

[os]
disabled = false
style = "bg:color_orange fg:color_fg0"

[os.symbols]
Windows = "󰍲"
Ubuntu = "󰕈"
SUSE = ""
Raspbian = "󰐿"
Mint = "󰣭"
Macos = "󰀵"
Manjaro = ""
Linux = "󰌽"
Gentoo = "󰣨"
Fedora = "󰣛"
Alpine = ""
Amazon = ""
Android = ""
Arch = "󰣇"
Artix = "󰣇"
CentOS = ""
Debian = "󰣚"
Redhat = "󱄛"
RedHatEnterprise = "󱄛"

[username]
show_always = true
style_user = "bg:color_orange fg:color_fg0"
style_root = "bg:color_orange fg:color_fg0"
format = '[ $user ]($style)'

[directory]
style = "fg:color_black bg:color_yellow"
format = "[ $path ]($style)"
truncation_length = 2
truncation_symbol = "…/"

[directory.substitutions]
"Documents" = "󰈙 "
"Downloads" = " "
"Music" = "󰝚 "
"Pictures" = " "
"Developer" = "󰲋 "

[git_branch]
symbol = ""
style = "bg:color_aqua"
format = '[[ $symbol $branch ](fg:color_fg0 bg:color_aqua)]($style)'

[git_status]
style = "bg:color_aqua"
format = '[[($all_status$ahead_behind )](fg:color_fg0 bg:color_aqua)]($style)'

[nodejs]
symbol = ""
style = "bg:color_blue"
format = '[[ $symbol( $version) ](fg:color_fg0 bg:color_blue)]($style)'

[c]
symbol = " "
style = "bg:color_blue"
format = '[[ $symbol( $version) ](fg:color_fg0 bg:color_blue)]($style)'

[rust]
symbol = ""
style = "bg:color_blue"
format = '[[ $symbol( $version) ](fg:color_fg0 bg:color_blue)]($style)'

[golang]
symbol = ""
style = "bg:color_blue"
format = '[[ $symbol( $version) ](fg:color_fg0 bg:color_blue)]($style)'

[php]
symbol = ""
style = "bg:color_blue"
format = '[[ $symbol( $version) ](fg:color_fg0 bg:color_blue)]($style)'

[java]
symbol = " "
style = "bg:color_blue"
format = '[[ $symbol( $version) ](fg:color_fg0 bg:color_blue)]($style)'

[kotlin]
symbol = ""
style = "bg:color_blue"
format = '[[ $symbol( $version) ](fg:color_fg0 bg:color_blue)]($style)'

[haskell]
symbol = ""
style = "bg:color_blue"
format = '[[ $symbol( $version) ](fg:color_fg0 bg:color_blue)]($style)'

[python]
symbol = ""
style = "bg:color_blue"
format = '[[ $symbol( $version) ](fg:color_fg0 bg:color_blue)]($style)'

[docker_context]
symbol = ""
style = "bg:color_bg3"
format = '[[ $symbol( $context) ](fg:#83a598 bg:color_bg3)]($style)'

[conda]
style = "bg:color_bg3"
format = '[[ $symbol( $environment) ](fg:#83a598 bg:color_bg3)]($style)'

[time]
disabled = false
time_format = "%T"
style = "bg:color_bg1"
format = '[[ $time ](fg:color_fg0 bg:color_bg1)]($style)'

[line_break]
disabled = false

[character]
disabled = false
success_symbol = '[](bold fg:color_green)'
error_symbol = '[](bold fg:color_red)'
vimcmd_symbol = '[](bold fg:color_green)'
vimcmd_replace_one_symbol = '[](bold fg:color_purple)'
vimcmd_replace_symbol = '[](bold fg:color_purple)'
vimcmd_visual_symbol = '[](bold fg:color_yellow)'

[gcloud]
detect_env_vars = ["gcpon"]
style = "bg:color_red fg:color_fg1"
format = '[  $account@$domain \[$project\]]($style)'

[kubernetes]
detect_env_vars = ["kubeon"]
style = "bg:color_sharp_blue fg:color_fg0"
format = '[ $symbol$context \[$namespace\]]($style)'
disabled = false
detect_files = []

[battery]
disabled = false
format = '[ $symbol$percentage]($style)'

[[battery.display]]
style = "bg:color_orange fg:color_fg0"
threshold = 100

[terraform]
style = "bg:color_purple fg:color_fg0"
format = '[ 󱁢 $version \[$workspace\]]($style)'

[status]
style = 'bg:color_orange'
symbol = '🔴'
success_symbol = '🟢'
format = '[ $symbol]($style)'
map_symbol = true
disabled = false

[custom.workspace]
command = "bash -c '[ -n \"$WORKSPACE_PROFILE\" ] && echo $WORKSPACE_PROFILE || echo $USER'"
when = "true"
style = "bg:color_orange fg:color_fg0"
format = "[ $output]($style)"

[sudo]
allow_windows = true
format = '[ $symbol]($style)'
style = 'bg:color_yellow fg:color_black'
symbol = '󰡗'
disabled = false
