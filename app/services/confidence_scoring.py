import re
from app.services.toc_parser import toc_to_linear_sequence


def score_toc_extraction(toc_tree) -> float:
    """Оценивает качество извлечённого оглавления (0.0 — 1.0)."""
    items = toc_to_linear_sequence(toc_tree)
    if not items:
        return 0.0

    # Доля пунктов с номером страницы
    with_page = sum(1 for i in items if i['page'] is not None)
    page_ratio = with_page / len(items)

    # Энтропия длин строк (низкая энтропия = хорошее форматирование ToC)
    title_lengths = [len(i['title']) for i in items]
    avg_len = sum(title_lengths) / len(title_lengths) if title_lengths else 0
    variance = sum((l - avg_len) ** 2 for l in title_lengths) / len(title_lengths) if title_lengths else 0
    std_dev = variance ** 0.5
    # Нормализуем: если std_dev < 10 — хорошее форматирование (1.0), > 100 — плохое (0.3)
    formatting_score = max(0.3, min(1.0, 1.0 - (std_dev / 200)))

    # Итоговый score: вес страницы важнее форматирования
    return round(max(0.0, min(1.0, page_ratio * 0.7 + formatting_score * 0.3)), 4)


def score_mapping(match_strategy: str, tokens_used: bool = False) -> float:
    """Оценивает уверенность маппинга по стратегии поиска."""
    if match_strategy == "exact":
        return 1.0
    elif match_strategy == "tokenized_regex":
        return 0.85
    elif match_strategy == "partial_words":
        return 0.6
    elif match_strategy == "num_prefix":
        return 0.4
    else:
        return 0.0


def count_low_confidence(mapped_items, threshold: float = 0.7) -> int:
    """Считает количество чанков ниже порога уверенности."""
    return sum(1 for m in mapped_items if m['confidence'] < threshold)
