# Запуск проекта

Двойной щелчок по `start-project.bat` в корне запускает PostgreSQL, Ollama,
API и frontend **без ngrok**. Повторный запуск использует те же сервисы.
При необходимости frontend собирается из текущего кода.

В открытой консоли: время UTC+5, HTTP-метод, шаблон маршрута, статус,
длительность в миллисекундах, Name и Id авторизованного пользователя.
Публичные запросы и отказы до авторизации показывают `Name="—" Id="—"`.
SSE показывает открытие и завершение потока. Пароли, cookies, query string,
тексты запросов и ключи публичных ссылок в журнал не попадают.

Журнал хранится в `.local/request-activity.jsonl`: до 5 MiB на файл и три
архива. Ctrl+C или закрытие консоли завершают просмотр; сервисы остаются
работать. `start-project.bat --once` запускает сервисы без просмотра журнала.
После Ctrl+C стандартный CMD может запросить `Y`: это завершает BAT, а не сервисы.
Если API уже работает без журнала, сначала остановите его и запустите BAT.

Из корня проекта:

```powershell
.\.venv\Scripts\python.exe scripts\local_db.py start
.\.venv\Scripts\python.exe scripts\local_llm.py
Start-Process -FilePath "$PWD\.venv\Scripts\python.exe" -ArgumentList '-m','app' -WorkingDirectory "$PWD\apps\api" -WindowStyle Hidden
Start-Process -FilePath 'C:\Program Files\nodejs\npm.cmd' -ArgumentList 'run','dev' -WorkingDirectory "$PWD\apps\web" -WindowStyle Hidden
```

Адреса:

- Frontend: `http://127.0.0.1:3000`
- API: `http://127.0.0.1:8000`
- PostgreSQL: `127.0.0.1:55432`
- Ollama: `http://127.0.0.1:11434`

Каталог VTO обновляется в фоне. Запуск не сбрасывает БД.

После обновления кода, перед первым запуском презентаций:

```powershell
.\.venv\Scripts\python.exe -m pip install -e "./apps/api[dev]"
.\.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
.\.venv\Scripts\python.exe scripts/setup_presentations.py
```

Подготовка фото и PDF выполняется отдельным ограниченным процессом API.
[Документация презентаций](presentations.md).

Ручное продолжение импорта через существующую очередь:

```powershell
.\.venv\Scripts\python.exe scripts/sync_vto.py
```

Остановка PostgreSQL:

```powershell
.\.venv\Scripts\python.exe scripts\local_db.py stop
```
