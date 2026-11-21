# Второй аудит: правки 19.11.2026

Независимый аудит коммита `cb28892` дал 11 замечаний. Каждое проверено по коду и сохранённым файлам; воспроизведения — короткими скриптами Python на Windows по запросам, переведённым из T-SQL в DuckDB (как в `tests/test_sql_offline.py`), и по копиям экспортов. SQL Server, ReportViewer, сервер SSRS и Power BI Desktop на Windows не запускались; SQL Server исполнялся только в CI (раздел «Прогон»).

## Второй аудит

| № | Замечание | Вердикт | Что сделано | Тест или причина |
|---|---|---|---|---|
| 1 | Дата окончания без снимка даёт нулевой запас через `COALESCE(b.OnHand, 0)` | подтверждено | `ReportRows` не возвращает строк, если на дату окончания нет снимка; новая процедура `SnapshotDates`; в RDL параметр «Период по» берёт значения из `SnapshotDates`, у таблицы есть `NoRowsMessage`; эталон `summarize` при такой дате завершается ошибкой. Частично неполный снимок по-прежнему даёт 0 — записано в рисках README и в STUDY | `test_end_date_without_snapshot_returns_no_rows` (DuckDB и SQL Server), `test_end_date_offers_only_snapshot_days` |
| 2 | STUDY: размножение строк без указания пропущенной даты | подтверждено | Ответ в STUDY переписан: по полному ключу строк не прибавляется, без дня — 430 094 строки; зачем агрегировать продажи и брать один снимок | Числа — воспроизведение ниже |
| 3 | «Пять процедур дают те же итоги» приписывает итоги справочникам | подтверждено | BRIEF, STUDY, README: что именно сверено у каждой процедуры; процедур теперь шесть | — |
| 4 | `check_render.py` не видит перестановку названий групп | подтверждено | Строки групп и товаров сверяются по пути «регион → магазин → категория → товар» | `test_swapped_region_names_fail`, `test_swapped_product_names_fail` |
| 5 | Заливка считается по всему листу, белый при остатке 8 не проверяется | подтверждено | Проверяется ячейка остатка каждой товарной строки (ниже 8 — `#FFE2C9`, иначе белая); оранжевая заливка вне ячеек остатка — ошибка | `test_low_stock_fill_outside_stock_cell_fails`, `test_stock_at_threshold_must_stay_white`, `test_group_row_with_low_stock_fill_fails` |
| 6 | Чужой PDF проходит, отсутствие `all.pdf` не ошибка | подтверждено | `all.pdf` обязателен; в каждом PDF — период, состав регионов, четыре значения печатных строк; отсутствующие — «content not checked». Четыре удалённых PDF проверены по копиям из коммита `e853c03` | `test_foreign_pdf_fails`, `test_missing_sample_pdf_fails`; вывод — `2026-11-19-render/check_render.txt` |
| 7 | Цена в SQL-тестах сравнивается одной и той же функцией | подтверждено | Цена отчёта проверяется по столбцу цены книг (1 812 ячеек: выручка / количество, пусто при нулевых продажах); в README цена SQL-тестам не приписывается. Сравнение `PriceCents` в SQL-тестах оставлено: ожидания не ослабляются, но доказательством формулы не считаются | `test_wrong_price_fails`, `test_price_for_unsold_product_must_be_empty` |
| 8 | README: `build-pbip` не меняет `DataFolder` | подтверждено | README и STUDY: три рабочих способа; `build-pbip` пишет значение константы `DEFAULT_DATA_FOLDER` | Проверено по `pbip.py` и `__main__.py` (у команды только `--project`) |
| 9 | Требование SSRS к пространству имён `ScheduleDefinition` не установлено | подтверждено | README, STUDY, PLAN: «совместимость расписания с SSRS не проверялась», без утверждения о требовании сервера | — |
| 10 | Числа промежуточных прогонов 18.11 без журналов | подтверждено | В протоколе SQL Server «6 тестов за 67,48 с» и «четыре ошибки» помечены как запись автора без сохранённого журнала | В `mssql-evidence.zip` только `final/` и `diagnostic/` |
| 11 | «Без предупреждений» не подтверждено выводом | подтверждено | В протоколе экспорта — запись автора, консольный вывод `Render.exe` не сохранён | В `replay.zip` вывода нет |

## Воспроизведение на Windows

Замечание 1 и 2, запрос `ReportRows` до правки (коммит `cb28892`), переведённый в DuckDB 1.5.5 над CSV с seed 39:

```
2026-01-31 rows 512 units 55523 onhand 8727
2026-02-01 rows 512 units 55523 onhand 0
stock days [(31, datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))]
join full key [(13874, 55523)] join without day [(430094, 1721213)]
```

После правки (коммит `99346f6`), те же CSV:

```
snapshot days 31 2026-01-01 2026-01-31
2026-01-31 rows 512 onhand 8727
2026-02-01 rows 0 onhand 0
summarize Feb 1 -> No stock snapshot on end date 2026-02-01
check_rdl datasets: 5
PASS {"scenarios": 5, "group_totals_checked": 148, "sales_rows": 13874, "stock_rows": 15872}
```

Последняя строка — `scripts/audit.py --verify-archive docs/runs/offline-evidence.zip`: архивный эталон не изменился.

Замечания 4–7, прежний `check_render.py` (коммит `cb28892`) на испорченных копиях: `True` означает, что порча прошла незамеченной.

```
unchanged first_day -> True
#4 swapped region names -> True
#5 fill moved to sales column -> True
#5 stock 8 painted blue -> True
#7 product price +1 -> True
#6 all.pdf as partial.pdf -> True
#6 all.pdf missing -> True
```

Переписанный скрипт (коммит `334fd59`) на тех же порчах: `False` и первая причина.

```
unchanged -> (True, [])
resaved first_day -> (True, [])
#4 swapped regions -> (False, xlsx group rows differ from reference by path ...)
#4 swapped products -> (False, xlsx product rows differ from replay by path ...)
#5 fill moved to sales column -> (False, xlsx C10: low-stock fill outside a stock cell ...)
#5 stock 8 painted blue -> (False, xlsx E107: stock 8 has fill FF9BC2E6)
#5 group row orange -> (False, xlsx E6: low-stock fill outside a stock cell)
#7 product price +0.01 -> (False, xlsx row 9 ...: price 46.2847368421053 != 46.27473684210527)
#7 price for zero units -> (False, xlsx row 10 ...: price 0 for zero units)
#6 all.pdf as partial.pdf -> (False, pdf has no period line 'Продажи и остатки за 08.01.2026 — 18.01.2026' ...)
#6 all.pdf missing -> (False, required sample all.pdf is missing)
```

Красный прогон тестов в CI отдельно не запускался: очередь к общей VM длинная, а падение старого кода показано воспроизведениями выше. Тесты закоммичены раньше правок (`cb1fb4c`, `1a582f2`).

## Граница экспортов

Сохранённые PDF и XLSX получены из RDL коммита `cb28892`, до правки. Все пять выборок заканчиваются датами со снимком (31, 18 и 1 января), поэтому ошибка замечания 1 в них не проявлялась: итоги совпадают с эталоном. Текущий RDL (набор `SnapshotDates`, `NoRowsMessage`) не отрисовывался ни ReportViewer, ни сервером SSRS.

## Прогон

Полный прогон ci_local `ssrs-sales-reports_20261119-121102` (Linux-контейнер, `--runner linux --docker`, обе задачи CI) на коммите `d2405ad` — код и тесты в той же редакции, что и в последнем коммите; после него менялись только документы. Итог по `state/summary.tsv`: обе задачи `JOB_ok`, ни одного шага FAIL. Предыдущий коммит `cb28892` полного прогона не имел; этот прогон его закрывает.

```
# ssrs-sales-reports_20261119-121102, state/summary.tsv
j1	5	PASS	7	Run python -m pytest -q -m 'not slow'
j1	6	PASS	1	Run python -m ssrs_lab generate && python -m ssrs_lab reference
j1	7	PASS	1	Run python -m ssrs_lab check-rdl && python -m ssrs_lab check-pbi
j1	8	PASS	1	Run python scripts/audit.py --verify-archive docs/runs/offline-e
j1	9	PASS	1	Run python scripts/check_render.py
j1	-	JOB_ok	34	ci/offline
j2	5	PASS	44	Start SQL Server
j2	6	PASS	11	Compare the procedures with the CSV reference
j2	7	PASS	22	Remove the stand
j2	-	JOB_ok	81	ci/mssql
```

```
# ssrs-sales-reports_20261119-121102, задача ci/offline, шаг 5, state/logs/j1_5.log
28 passed, 12 deselected in 6.31s
# шаг 7, state/logs/j1_7.log
RDL datasets verified: 5
PBIR visuals verified: 5
# шаг 8, state/logs/j1_8.log
PASS {"scenarios": 5, "group_totals_checked": 148, "sales_rows": 13874, "stock_rows": 15872}
# шаг 9, state/logs/j1_9.log
all: total [55523, 384014558, 8727] groups=42/42 products=512/512 prices=555 stock<8 filled=73/73 stock==8 white=20/20 pdf pages=1 size=595x842pt rows=3 -> ok
north: ... pdf content not checked (not in repository, SHA-256 in the render record) -> ok
two_categories: ... -> ok
partial: ... -> ok
first_day: ... -> ok
5/5 cases passed: PASS
# ssrs-sales-reports_20261119-121102, задача ci/mssql, шаг 6, state/logs/j2_6.log
12 passed in 10.40s
```

В задаче `offline` 28 тестов: прежние 16, по одному новому в `test_sql_offline.py` и `test_reports.py`, десять в `test_check_render.py`. В задаче `mssql` 12 тестов: прежние 11 и `test_end_date_without_snapshot_returns_no_rows` — на SQL Server `SnapshotDates` вернула 31 дату января, `ReportRows` с датой окончания 31 января дала остаток 8 727, с датой 1 февраля — пустой результат. Журналы прогона в репозиторий не копировались; выдержки выше перенесены из них дословно.
