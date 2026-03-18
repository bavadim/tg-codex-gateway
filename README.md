# TG Agent Gateway

Telegram-бот, который запускает `opencode` на локальной папке проекта. Нужен, чтобы работать с проектом из Telegram: задавать вопросы по коду, запускать агент на своей рабочей директории и, при необходимости, управлять GitHub issues и GitHub Projects через `gh`.

## Установка

```bash
python -m pip install "git+https://github.com/bavadim/tg-codex-gateway.git"
```

## Что нужно настроить

Нужны:

- `opencode`
- `git`
- `gh`
- Telegram bot token
- `.env` с переменными
- SSH-ключ пользователя, от имени которого бот будет пушить изменения

Установка инструментов:

```bash
curl -fsSL https://opencode.ai/install | bash
sudo apt install git
sudo apt install gh
```

### Telegram bot token

1. Открой `@BotFather` в Telegram.
2. Выполни `/newbot`.
3. Задай имя и username бота.
4. Скопируй выданный token в `TELEGRAM_BOT_TOKEN`.

### Переменные окружения

Создай `.env`:

```bash
cp env.example .env
```

Заполни как минимум:

```bash
TELEGRAM_BOT_TOKEN=123456:your-bot-token
ALLOWED_CHAT_USER_IDS=@your_username
AGENT_BACKEND=opencode
OPENCODE_BIN=opencode
OPENCODE_MODEL=myopenai/compressa1
OPENAI_API_BASE=http://console.insightstream.ru:8080/v1
OPENAI_API_KEY=your-api-key
GH_TOKEN=your-github-token
```

Кратко:

- `TELEGRAM_BOT_TOKEN` — токен бота от `@BotFather`
- `ALLOWED_CHAT_USER_IDS` — кто может пользоваться ботом
- `OPENAI_API_BASE` и `OPENAI_API_KEY` — настройки провайдера для `opencode`
- `OPENCODE_MODEL` — model id в формате `provider/model`; для этого провайдера рабочий пример: `myopenai/compressa1`
- `GH_TOKEN` — GitHub token для `gh`, нужен для headless-запуска или если бот должен работать без интерактивного `gh auth login`

Gateway сам собирает runtime-конфиг для `opencode` из env. Дополнительный `opencode.json` в проекте не нужен.
Gateway также принудительно включает для `opencode` режим без интерактивных permission prompts внутри `--workdir`: читать, редактировать файлы и запускать команды в проекте можно без дополнительных подтверждений. Доступ за пределы рабочей директории gateway не открывает.
Gateway также принудительно использует только агент `build`: режим `plan` отключен и не должен использоваться.
Для OpenAI-compatible провайдеров указывай `OPENAI_API_BASE` как базовый URL API, например `http://host:port/v1`. Суффикс `/responses` дописывать не нужно: `opencode` делает это сам.

## GitHub: вход под нужным пользователем

`gh` нужно логинить под тем GitHub-пользователем, от имени которого бот будет:

- создавать и редактировать issues
- менять items в GitHub Projects
- пушить коммиты и открывать PR, если агент правит код

Проверь, кто сейчас активен:

```bash
gh auth status
```

Если нужен другой пользователь, сначала разлогинь текущего:

```bash
gh auth logout
```

Потом залогинь нужный аккаунт:

```bash
gh auth login --web --git-protocol ssh
```

Если `gh` уже залогинен, но не хватает доступа к GitHub Projects, добавь scope:

```bash
gh auth refresh -s project
```

По документации GitHub CLI, для `gh project` нужен scope `project`, а для работы через `gh auth login --with-token` minimum classic scopes для `gh` включают `repo`, `read:org` и `gist`. Для добавления issue в GitHub Project через `gh issue create` тоже нужен `project`.

## Как получить токен для `gh`

Есть два рабочих варианта.

### Вариант 1. Через `gh auth login` в браузере

Это самый простой способ для локальной машины:

```bash
gh auth login --web --git-protocol ssh
gh auth refresh -s project
```

Так `gh` сохранит учетные данные локально. Этот вариант удобен, если gateway запускается в пользовательской сессии.

### Вариант 2. Через Personal Access Token

Это удобно для headless-запуска, `systemd` и серверов. Тогда положи токен в `GH_TOKEN`.

#### Classic PAT

1. Открой GitHub: `Settings` -> `Developer settings` -> `Personal access tokens` -> `Tokens (classic)`.
2. Нажми `Generate new token (classic)`.
3. Задай понятное имя, срок жизни и выбери scopes:
4. Для работы с приватными репозиториями и issues обычно нужен `repo`.
5. Для GitHub Projects нужен `project`.
6. Для части операций в организациях часто нужен `read:org`.
7. Скопируй токен один раз и положи его в `.env` как `GH_TOKEN=...`.

Минимально практичный набор для этого gateway:

- `repo`
- `project`
- `read:org`

`gist` нужен для минимального дефолтного набора `gh auth login --with-token`, но для самого gateway обычно не является целевым разрешением. Если используешь именно `gh auth login --with-token`, учитывай это требование CLI.

#### Fine-grained PAT

Можно использовать и fine-grained token, но GitHub CLI отдельно предупреждает, что при `--with-token` он иногда ведет себя менее предсказуемо из-за узкой привязки к ресурсам. Для automation лучше либо:

- экспортировать `GH_TOKEN` напрямую
- либо использовать classic PAT, если нужен максимально простой и совместимый сценарий

Если все же делаешь fine-grained token:

1. Открой `Settings` -> `Developer settings` -> `Personal access tokens` -> `Fine-grained tokens`.
2. Выбери владельца токена, нужные репозитории и срок жизни.
3. Выдай как минимум:
4. repository permission `Issues: Read and write`
5. repository permission `Pull requests: Read and write`, если бот будет работать с PR
6. organization permission `Projects: Read and write`, если бот должен менять items в GitHub Projects
7. Сохрани токен в `GH_TOKEN`.

Если организация требует approve fine-grained PAT, токен должен быть дополнительно одобрен администратором организации.

## Что нужно для правки кода ботом

Чтобы бот мог не только читать проект, но и коммитить/пушить изменения, на машине должны быть готовы обычные git-доступы пользователя, от имени которого работает gateway.

Нужно:

- установленный `git`
- настроенные `git config user.name` и `git config user.email`
- SSH-ключ этого пользователя
- публичный ключ добавлен в GitHub-аккаунт или в deploy keys, в зависимости от вашей схемы доступа
- репозиторий должен быть склонирован по SSH, например `git@github.com:owner/repo.git`

Типовой сценарий:

```bash
ssh-keygen -t ed25519 -C "bot-user@example.com"
cat ~/.ssh/id_ed25519.pub
```

После этого:

1. Добавь публичный ключ в GitHub пользователя, под которым бот будет работать.
2. Проверь доступ: `ssh -T git@github.com`
3. Проверь, что у репозитория SSH remote, а не HTTPS: `git remote -v`

Если gateway запущен как отдельный системный пользователь или сервис, SSH-ключ должен лежать именно в его домашней директории, и именно этот пользователь должен иметь доступ к репозиторию.

## Запуск

```bash
set -a
source .env
set +a
tg-agent-gateway --workdir /path/to/your/project
```

## Что важно

- `--workdir` должен указывать на папку проекта
- если в проекте есть `AGENTS.md` и `.opencode/skills`, `opencode` их увидит
- вместе с gateway поставляется skill `gh-pm` для управления GitHub issues и GitHub Projects через `gh`
- для headless-сценария удобно хранить `GH_TOKEN` в `.env`, но для локального интерактивного запуска обычно достаточно `gh auth login`
- если бот должен пушить код, проверь `git`, SSH-ключ и `origin` по SSH заранее
