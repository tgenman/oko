on run
    set appPath to POSIX path of (path to me)
    set projectPath to do shell script "/usr/bin/dirname " & quoted form of appPath
    set pythonPath to projectPath & "/.venv/bin/python"
    set telegramEnv to "/Users/dabondarev/repo_personal/telegram-mcp/.env"
    set logPath to projectPath & "/data/launcher.log"
    try
        do shell script "/bin/test -x " & quoted form of pythonPath
        do shell script "/bin/test -r " & quoted form of telegramEnv
        set launchCommand to "cd " & quoted form of projectPath & " && umask 077 && /bin/mkdir -p data && PATH=/Users/dabondarev/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin /usr/bin/nohup " & quoted form of pythonPath & " -m oko --telegram-env " & quoted form of telegramEnv & " > " & quoted form of logPath & " 2>&1 < /dev/null & launch_pid=$!; /bin/sleep 1; if ! /bin/kill -0 \"$launch_pid\" 2>/dev/null; then wait \"$launch_pid\"; fi"
        do shell script launchCommand
    on error
        display dialog "Не удалось запустить Око. Проверьте, что приложение лежит в папке проекта, окружение .venv установлено и файл авторизации Telegram доступен. Журнал запуска: " & logPath buttons {"OK"} default button "OK" with icon caution
    end try
end run
