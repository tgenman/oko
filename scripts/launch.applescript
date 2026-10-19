on run
    set pythonCode to "import json, re, subprocess, sys; from pathlib import Path; home = Path.home() / 'Library/Application Support/Oko'; release = json.loads((home / 'current.json').read_text())['release']; assert re.fullmatch('[0-9a-f]{40}', release), 'Invalid installed release'; controller = home / 'releases' / release / 'source/scripts/oko-release'; [subprocess.run([sys.executable, str(controller), '--home', str(home), operation], check=True) for operation in ('start', 'open')]"
    try
        do shell script "/usr/bin/env PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin /usr/bin/python3 -c " & quoted form of pythonCode
    on error
        display dialog "Не удалось открыть рабочее Око. Проверьте установку выпуска и запуск Docker Desktop. Адрес читателя: http://localhost:8765. Журналы находятся в ~/Library/Application Support/Oko/logs/." buttons {"OK"} default button "OK" with icon caution
    end try
end run
