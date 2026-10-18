import argparse
import logging
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass

DEFAULT_CHUNK_SIZE = 64 * 1024
BYTES_PER_MIB = 1024 * 1024
BITS_PER_BYTE = 8

REQUEST_HEADERS = {
    "User-Agent": "python-speedtest",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

logger = logging.getLogger("speedtest")


@dataclass(frozen=True, slots=True)
class RequestResult:
    """Результат одной успешной загрузки."""

    bytes_read: int
    elapsed_s: float

    @property
    def mib_per_s(self) -> float:
        return (self.bytes_read / BYTES_PER_MIB) / self.elapsed_s


@dataclass(frozen=True, slots=True)
class Stats:
    """Агрегированные показатели серии загрузок."""

    attempts: int
    successes: int
    total_bytes: int
    total_time_s: float
    avg_time_s: float
    median_time_s: float
    mib_per_s: float
    mbit_per_s: float


def positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("должно быть положительным числом")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Замер скорости интернета путём последовательных загрузок файла по URL.",
        epilog="Пример: speedtest.py https://example.com/heavy.jpg -n 10",
    )
    parser.add_argument("url", help="Адрес файла для скачивания (например, тяжёлая картинка)")
    parser.add_argument(
        "-n", "--count", type=positive_int, default=10,
        help="Количество запросов (по умолчанию 10)",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0,
        help="Таймаут одного запроса в секундах (по умолчанию 30)",
    )
    parser.add_argument(
        "--chunk-size", type=positive_int, default=DEFAULT_CHUNK_SIZE,
        help=f"Размер буфера чтения в байтах (по умолчанию {DEFAULT_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--warmup", action="store_true",
        help="Прогревочный запрос вне статистики: снимает разовые издержки первого обращения",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Подробный вывод (DEBUG)")
    return parser.parse_args(argv)


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def format_mib(num_bytes: float) -> str:
    return f"{num_bytes / BYTES_PER_MIB:.2f} МиБ"


def download_once(url: str, timeout: float, chunk_size: int) -> RequestResult:
    """Скачивает ресурс целиком и возвращает объём и время.

    Тело ответа не сохраняется и не накапливается в памяти: иначе замер
    включал бы скорость диска либо упирался бы в RAM на больших файлах.
    """
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    bytes_read = 0
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        while chunk := response.read(chunk_size):
            bytes_read += len(chunk)
    return RequestResult(bytes_read=bytes_read, elapsed_s=time.perf_counter() - start)


def run_requests(url: str, count: int, timeout: float, chunk_size: int) -> list[RequestResult]:
    """Выполняет count последовательных загрузок: скипает, не прерывая серию."""
    results: list[RequestResult] = []
    for attempt in range(1, count + 1):
        logger.debug("Запрос %d/%d: GET %s", attempt, count, url)
        try:
            result = download_once(url, timeout, chunk_size)
        except OSError as error:
            logger.error("Запрос %d/%d: ошибка: %s", attempt, count, error)
            continue

        results.append(result)
        logger.info(
            "Запрос %d/%d: %s за %.3f с (%.2f МиБ/с)",
            attempt, count, format_mib(result.bytes_read), result.elapsed_s, result.mib_per_s,
        )
    return results


def warm_up(url: str, timeout: float, chunk_size: int) -> None:
    logger.info("Прогревочный запрос (в статистику не входит)")
    try:
        download_once(url, timeout, chunk_size)
    except OSError as error:
        logger.warning("Прогревочный запрос не удался: %s", error)


def summarize(results: list[RequestResult], attempts: int) -> Stats:
    """Считает агрегаты по успешным загрузкам.

    Итоговая скорость — total_bytes / total_time, а не среднее скоростей отдельных
    запросов: среднее арифметическое по отношениям завышает вклад коротких быстрых
    запросов и не равно фактической пропускной способности канала.
    """
    times = [result.elapsed_s for result in results]
    total_bytes = sum(result.bytes_read for result in results)
    total_time = sum(times)
    return Stats(
        attempts=attempts,
        successes=len(results),
        total_bytes=total_bytes,
        total_time_s=total_time,
        avg_time_s=total_time / len(results),
        median_time_s=statistics.median(times),
        mib_per_s=(total_bytes / BYTES_PER_MIB) / total_time,
        mbit_per_s=(total_bytes * BITS_PER_BYTE / 1_000_000) / total_time,
    )


def format_report(stats: Stats) -> str:
    return "\n".join([
        "--- Итоги ---",
        f"Успешных запросов:     {stats.successes}/{stats.attempts}",
        f"Среднее время запроса: {stats.avg_time_s:.3f} с",
        f"Медиана времени:       {stats.median_time_s:.3f} с",
        f"Всего скачано:         {format_mib(stats.total_bytes)} за {stats.total_time_s:.3f} с",
        f"Скорость:              {stats.mib_per_s:.2f} МиБ/с ({stats.mbit_per_s:.1f} Мбит/с)",
    ])


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)

    logger.info("Замер скорости: %s (%d запросов)", args.url, args.count)
    if args.warmup:
        warm_up(args.url, args.timeout, args.chunk_size)

    results = run_requests(args.url, args.count, args.timeout, args.chunk_size)
    if not results:
        logger.error("Ни один запрос не выполнен успешно")
        return 1

    print(format_report(summarize(results, args.count)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
