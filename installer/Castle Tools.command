#!/bin/sh
# Double-click to start Castle Tools and open it in the browser.
#
# The installer copies this file into the install folder (on a Mac,
# ~/Applications/CastleTools). Everything it does is
# tools/desktop_launch.py under the installed environment: a second launch
# reuses the running server, and it never installs anything. Close this
# window, or press Ctrl-C, to stop.
unset CDPATH
here=$(cd -- "$(dirname -- "$0")" && pwd)
root=${CASTLE_TOOLS_HOME:-}
if [ -z "$root" ]; then
	if [ -x "$here/env/bin/python" ]; then
		root=$here
	elif [ "$(uname -s)" = Darwin ]; then
		root="$HOME/Applications/CastleTools"
	else
		root="$HOME/.local/opt/CastleTools"
	fi
fi
py="$root/env/bin/python"
if [ ! -x "$py" ]; then
	echo "Castle Tools are not installed in $root."
	echo "Run installer/install.sh from the Castle Tools folder first."
	printf "Press return to close. "
	read -r _
	exit 1
fi
"$py" "$root/app/tools/desktop_launch.py" "$@" || {
	printf "Press return to close. "
	read -r _
	exit 1
}
