# История операций по счёту — REST API

Дочерний документ к ТЗ «История операций в мобильном банке» (версия 2.0). Описывает метод получения списка операций по счёту клиента с фильтрами и постраничной выдачей.

### Общие параметры

| Поле | Значение |
| :--- | :--- |
| **Description** | История операций по счёту за последние 12 месяцев с фильтрами по периоду, типу и сумме, постраничная выдача |
| **REST type** | GET |
| **Authorization** | Bearer (JWT) в заголовке Authorization, сессия владельца счёта |
| **Controller** | AccountHistoryController |
| **URL type** | Dev / Prod |
| **Request URL** | Dev: `https://api-dev.bank.local/v2/accounts/{accountId}/transactions`. Prod: `https://api.bank.local/v2/accounts/{accountId}/transactions` |
| **CURL** | `curl "$BASE/v2/accounts/acc-7f3a9c/transactions?dateFrom=2026-10-01&dateTo=2026-10-31&page=1&size=50&access_token=$TOKEN" -H "Content-Type: application/json" -d '{"types":["PURCHASE","TRANSFER"]}'` |
| **Swagger** | `https://api-dev.bank.local/swagger/history` |
| **Gitlab project** | retail/history-service |
| **Framework** | Spring Boot 3 |
| **Author** | Системный аналитик команды «История операций» |
| **Developer** | Команда «История операций» |

### Логика работы метода
1. Проверить токен доступа и сессию клиента.
2. Проверить, что счёт `accountId` принадлежит клиенту из токена. Если счёт не найден или принадлежит другому клиенту — вернуть 404: так по ответу нельзя узнать, существует ли чужой счёт.
3. Проверить параметры фильтра.
4. Выбрать операции по счёту за период, отфильтровать по типам и сумме, отсортировать по дате операции по убыванию, при равной дате — по `operationId`.
5. Вернуть страницу операций и общее количество операций по фильтру.
6. Если операций нет — вернуть 404 «Операции не найдены».

### Особые правила
1. Доступна история за последние 24 месяца. Более ранние операции клиент получает через заказ выписки.
2. Период между dateFrom и dateTo — не более 92 дней.
3. На странице — не более 100 операций.
4. Суммы передаются строкой с двумя знаками после точки, например `"1250.00"`, чтобы избежать ошибок округления чисел с плавающей точкой. Списания — со знаком «-».
5. Авторизованные, но ещё не списанные операции возвращаются со статусом `PENDING`.

### Схема
```text
Приложение → AccountHistoryController → сервис счетов (проверка владельца) → хранилище истории → AccountHistoryController → приложение.
```

### Параметры запроса

#### Path-параметры

| Parameter | Format | Size | Mandatory | Logic | Description | DB field |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `accountId` | string | 1–36 | Да | Счёт клиента из токена | Внутренний идентификатор счёта | `ACCOUNTS.ACC_ID` |

#### Query-параметры

| Parameter | Format | Size | Mandatory | Logic | Description | DB field |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `dateFrom` | string | 10 | Да | Формат DD.MM.YYYY | Начало периода | `OPS_HIST.OP_DATE` |
| `dateTo` | string | 10 | Да | Формат DD.MM.YYYY, не раньше dateFrom | Конец периода | `OPS_HIST.OP_DATE` |
| `amountMin` | string | до 15 | Нет | От `"0.00"`; сравнивается с модулем суммы операции в валюте счёта | Минимальная сумма операции | `OPS_HIST.AMOUNT` |
| `amountMax` | string | до 15 | Нет | Не меньше amountMin | Максимальная сумма операции | `OPS_HIST.AMOUNT` |
| `page` | integer | 32 bit | Нет | Номер страницы, начиная с 1; по умолчанию 1 | Номер страницы | — |
| `size` | integer | 32 bit | Нет | 1–1000, по умолчанию 50 | Размер страницы | — |

#### Request body
* **Content-Type:** `application/json`.
* Тело: `{"types": ["PURCHASE", "TRANSFER"]}` — список типов операций для фильтра. Если тело не передано — возвращаются все типы.

### Ответ
Успешный ответ — 200. Ответы 400, 401, 404 и 500 — неуспешные.

| Parameter | Format | Logic | Description | DB field |
| :--- | :--- | :--- | :--- | :--- |
| `page` | integer | Номер текущей страницы | Страница | — |
| `size` | integer | Размер страницы из запроса | Размер страницы | — |
| `total` | integer | Число операций по фильтру | Всего операций | — |
| `items` | array | Операции текущей страницы | Список операций | — |
| `items[].operationId` | string | UUID | Идентификатор операции | `OPS_HIST.OP_ID` |
| `items[].operationDate` | string | ISO 8601 с часовым поясом, например `2026-10-15T14:32:10+05:00` | Дата и время операции | `OPS_HIST.OP_DATE` |
| `items[].type` | string | PURCHASE, TRANSFER, CASH, FEE и т. д. | Тип операции | `OPS_HIST.OP_TYPE` |
| `items[].amount` | string | Строка с двумя знаками после точки | Сумма в валюте счёта | `OPS_HIST.AMOUNT` |
| `items[].currency` | string | ISO 4217, например `KZT` | Валюта счёта | `OPS_HIST.CCY` |
| `items[].status` | string | `DONE` / `PENDING` | Статус операции | `OPS_HIST.STATUS` |
| `items[].description` | string | До 140 символов | Назначение или название торговой точки | `OPS_HIST.DESCR` |
| `items[].cardNumber` | string | 16 цифр | Номер карты, по которой прошла операция | `CARDS.PAN` |

### Статусы

**200.** Ответ в случае успешного выполнения метода.  
**400.** Ответ в случае, если параметры запроса заполнены неверно.  
**401.** Ответ в случае, если токен отсутствует или истёк.  
**404.** Ответ в случае, если счёт не найден или по фильтру нет операций.  
**500.** Ответ в случае внутренней ошибки сервиса.

#### 200. Successful
```json
{"page":1,"size":50,"total":2,"items":[
  {"operationId":"0b6f2c1e-8d4a-4f7e-9a51-3c2d7e8f9a10","date":"2026-10-15T14:32:10+05:00","type":"PURCHASE","amount":"-1250.00","currency":"KZT","status":"DONE","description":"Кофейня","cardNumber":"4000001234567899"},
  {"operationId":"5e9a7b3d-1c2f-4a6b-8e0d-7f1a2b3c4d5e","date":"2026-10-14T09:05:44+05:00","type":"TRANSFER","amount":"50000.00","currency":"KZT","status":"DONE","description":"Перевод от клиента","cardNumber":"4000001234567899"}
]}
```

#### 400. Unsuccessful — Bad request
```json
{"message":"Неверные параметры запроса"}
```

#### 401. Unsuccessful — Unauthorized
```json
{"message":"Сессия недействительна"}
```

#### 404. Unsuccessful — Not found
```json
{"message":"Счёт не найден"}
```
или
```json
{"message":"Операции не найдены"}
```

#### 500. Unsuccessful — Internal server error
```json
{"message":"Внутренняя ошибка"}
```
