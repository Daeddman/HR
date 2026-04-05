# HR Monitor — Бот мониторинга ликвидаций в DeFi

Автоматический Python-бот для мониторинга **Health Factor (HF)** позиций в lending протоколах на сетях **Arbitrum**, **BNB Smart Chain (BSC)** и **Base**. Уведомляет в Telegram о позициях, доступных для ликвидации.

---

## Поддерживаемые протоколы

| Протокол | Сеть | Контракт |
|---|---|---|
| Aave v3 | Arbitrum | `0x794a61358D6845594F94dc1DB02A252b5b4814aD` |
| Aave v3 | Base | `0xA238Dd80C259a72e81d7e4664a9801593F98d1c5` |
| Radiant Capital v2 | Arbitrum | `0xF4B1486DD74D07706052A33d31d7c0AAFD0659E1` |
| Compound v3 (USDC) | Arbitrum | `0x9c4ec768c28520B50860ea7a15bd7213a9fF58bf` |
| Compound v3 (USDC) | Base | `0xb125E6687d4313864e53df431d5425969c15Eb2` |
| Venus | BSC | `0xfD36E2c2a6789Db23113685031d7F16329158384` |
| Seamless Protocol | Base | `0x8F44Fd754285aa6A2b8B9B97739B79746e0475a7` |
| Moonwell | Base | `0xfBb21d0380beE3312B33c4353c8936a0F13EF26C` |

---

## Установка

```bash
pip install -r requirements.txt
```

---

## Настройка `.env`

Скопируйте `.env.example` в `.env` и заполните:

```bash
cp .env.example .env
```

| Параметр | Описание | По умолчанию |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота (от @BotFather) | — |
| `TELEGRAM_CHAT_ID` | ID чата/канала для уведомлений | — |
| `ARB_RPC` | RPC для Arbitrum | `https://arb1.arbitrum.io/rpc` |
| `BSC_RPC` | RPC для BSC | `https://bsc-dataseed.binance.org/` |
| `BASE_RPC` | RPC для Base | `https://mainnet.base.org` |
| `HF_ALERT_THRESHOLD` | Порог HF для уведомлений | `1.05` |
| `HF_CRITICAL_THRESHOLD` | Критический порог HF | `1.01` |
| `SCAN_INTERVAL` | Интервал сканирования (сек) | `60` |
| `MIN_POSITION_USD` | Минимальный долг позиции (USD) | `1000` |
| `ALERT_COOLDOWN_MINUTES` | Кулдаун повторных уведомлений (мин) | `10` |
| `MAX_BLOCKS_SCAN` | Блоков для поиска заёмщиков при старте | `50000` |
| `LOG_CHUNK_SIZE` | Блоков в одном запросе `eth_getLogs` (против 413) | `2000` |
| `LOG_CHUNK_DELAY` | Пауза (сек) между чанками `eth_getLogs` (против 429) | `0.2` |
| `LOG_MAX_RETRIES` | Повторных попыток при ошибке 429 | `5` |
| `LOG_RETRY_BASE_DELAY` | Базовая задержка backoff при 429 (сек) | `2.0` |

---

## Запуск

```bash
python -m hr_monitor.main
```

или

```bash
python hr_monitor/main.py
```

---

## Telegram команды

| Команда | Описание |
|---|---|
| `/start` | Приветствие и описание бота |
| `/status` | Статус мониторинга (активные протоколы, время последнего скана) |
| `/threshold` | Текущие пороги HF |
| `/setthreshold <value>` | Изменить порог HF_ALERT_THRESHOLD (например `/setthreshold 1.1`) |
| `/stats` | Статистика: сколько позиций найдено за сессию |

---

## Пример уведомления

```
🔴 ЛИКВИДАЦИЯ ДОСТУПНА

📋 Протокол: Aave v3
🌐 Сеть: Arbitrum
👤 Адрес: 0x1234...5678
❤️ Health Factor: 0.987
💰 Залог: $15,420.50
💸 Долг: $14,200.00
💥 Shortfall: $780.00
🎁 Бонус ликвидатора: 5.0%

🔗 Explorer: https://arbiscan.io/address/0x1234...5678
⏰ 2026-04-03 17:35:00 UTC
```

При критическом HF (< `HF_CRITICAL_THRESHOLD`):

```
🚨 КРИТИЧНО — HF < 1.01

📋 Протокол: Aave v3
...
```

---

## Архитектура

```
hr_monitor/
├── main.py                  # Точка входа, основной цикл
├── config.py                # Конфигурация (загрузка .env)
├── bot.py                   # Telegram бот (aiogram 3.x)
├── monitor.py               # Основной менеджер мониторинга
├── protocols/
│   ├── base_protocol.py     # Абстрактный базовый класс
│   ├── aave_v3.py           # Aave v3 (ARB, BASE)
│   ├── compound_v3.py       # Compound v3 / Comet (ARB, BASE)
│   ├── venus.py             # Venus (BSC)
│   ├── radiant.py           # Radiant Capital (ARB)
│   ├── seamless.py          # Seamless Protocol (BASE)
│   └── moonwell.py          # Moonwell (BASE)
├── chains/
│   └── rpc.py               # RPC провайдеры для ARB/BSC/BASE
└── utils/
    └── logger.py            # Логирование
```

---

## Требования

- Python 3.10+
- `web3>=6.0.0`
- `aiogram>=3.0.0`
- `python-dotenv>=1.0.0`
- `aiohttp>=3.9.0`
