# semantic-memory Specification

## Purpose
Версионная семантическая память кампании: Events (append-only), Knowledge, Strategies,
Edges с ролями Reader (TASK/ANALYSIS), Writer (PREPARE→EVALUATE→COMMIT) и Controller
(применимость + курация). Публикация атомарна под branch fence; scope обязателен везде.

## Requirements

### Requirement: Четыре типа сущностей и append-only Events

Память SHALL хранить Events, Knowledge, Strategies, Edges. Events MUST быть append-only с
уникальностью по (event_id, hash). Knowledge и Strategies MUST быть версионными с
условиями, основаниями и контрпримерами; Edges MUST нести тип, версию и происхождение связи.

#### Scenario: Повторный append того же события не дублирует

- **WHEN** событие с тем же event_id и hash добавляется повторно
- **THEN** возвращается прежняя EventReceipt без создания дубликата (CONFLICT по несовпадению hash)

### Requirement: Два режима чтения и обязательный scope

Reader SHALL возвращать данные в режиме TASK (текущая задача) или ANALYSIS (курация). Оба
режима MUST фильтровать по target/branch/version/доступу. ANALYSIS может включать устаревшие
и конфликтующие записи своей ветки, но MUST NOT открывать чужую ветку.

#### Scenario: TASK не видит кандидатов preview

- **WHEN** выполняется обычное TASK-чтение
- **THEN** непубличные candidate-preview не попадают в результат

#### Scenario: Чтение не пересекает границу ветки

- **WHEN** Reader вызывается в scope ветки A
- **THEN** записи ветки B не возвращаются ни в TASK, ни в ANALYSIS

### Requirement: Единый Write-путь и атомарная публикация

Публиковать в память SHALL только Writer, атомарно под блокировкой ветки, после повторной
проверки сохранённого approval по candidate hash. Controller MUST NOT писать напрямую;
проверенный кандидат передаётся Writer. Блокировка MUST удерживаться кратко, без LLM и сети.

#### Scenario: Controller не пишет напрямую

- **WHEN** Controller сформировал план курации
- **THEN** изменения применяет Writer через проверку кандидата, а не Controller напрямую

#### Scenario: Повторная публикация того же кандидата возвращает прежнюю квитанцию

- **WHEN** другой worker уже закоммитил тот же candidate hash под той же базой
- **THEN** возвращается прежняя PublicationReceipt, а не ложный VERSION_CONFLICT

#### Scenario: Изменение состава аннулирует approval

- **WHEN** после одобрения меняется candidate hash, база или read_set
- **THEN** старое approval неприменимо и требуется новая оценка

### Requirement: Производность векторного поиска и точный fallback

pgvector/ANN SHALL быть производным способом поиска, не источником истины. Фильтр по scope
и версии MUST применяться ко всем путям (SQL, ANN, кэш, Edges, сводка, blob, preview). При
нехватке результатов после фильтрации SHALL использоваться точный scoped fallback или
возвращаться INCOMPLETE; выдача чужих записей MUST NOT происходить.

#### Scenario: Недостаток ANN-результатов не раскрывает чужие данные

- **WHEN** после фильтрации по scope ANN вернул меньше результатов, чем запрошено
- **THEN** выполняется точный scoped fallback либо возвращается INCOMPLETE, без чужих записей
