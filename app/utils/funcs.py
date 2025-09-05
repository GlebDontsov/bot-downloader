def format_file_size(total_size: int) -> str:
    """Форматирует размер файла в удобочитаемый вид."""
    if total_size > 1024 * 1024 * 1024:
        return f"{total_size / (1024 * 1024 * 1024):.1f} ГБ"
    elif total_size > 1024 * 1024:
        return f"{total_size / (1024 * 1024):.1f} МБ"
    elif total_size > 1024:
        return f"{total_size / 1024:.1f} КБ"
    else:
        return f"{total_size} Б"
