"""Исправляет assignments.json: экранирует переносы строк в строках."""
import json
import re


def fix_json_strings(content: str) -> str:
    """Заменяет переносы строк внутри JSON-строк на \\n."""
    result = []
    i = 0
    in_string = False
    escape_next = False
    quote_char = None

    while i < len(content):
        c = content[i]
        if escape_next:
            result.append(c)
            escape_next = False
            i += 1
            continue
        if c == "\\" and in_string:
            result.append(c)
            i += 1
            if i < len(content):
                next_c = content[i]
                if next_c == "\n":
                    result.append("n")  # \n -> \n в JSON
                elif next_c == "\r":
                    result.append("r")
                else:
                    result.append(next_c)
                i += 1
            continue
        if c == '"' and not in_string:
            in_string = True
            quote_char = c
            result.append(c)
            i += 1
            continue
        if c == quote_char and in_string:
            # Закрывающая кавычка: за ней идёт ", " (след. ключ) или " } или " : или } или ,
            j = i + 1
            while j < len(content) and content[j] in " \t\n\r":
                j += 1
            rest = content[j:j+20]  # смотрим вперёд
            # Паттерн ", "key" или ", } или ": " — закрываем
            is_closing = (
                (rest.startswith(",") and ("," in rest[1:4] or '"' in rest[1:10])) or
                rest.startswith("}") or
                rest.startswith(":") or
                (rest.startswith('"') and len(rest) > 1 and rest[1].isdigit())  # "1" или "dop"
            )
            if not is_closing:
                # Проверяем ", \n    " — типичный паттерн перед ключом
                k = j
                while k < len(content) and content[k] in " \t\n\r,":
                    k += 1
                if k < len(content) and content[k] == '"' and (content[k:k+4] == '"1"' or content[k:k+4] == '"2"' or content[k:k+4] == '"3"' or content[k:k+4] == '"4"' or content[k:k+4] == '"5"' or content[k:k+4] == '"6"' or content[k:k+4] == '"7"' or content[k:k+4] == '"8"' or content[k:k+4] == '"9"' or content[k:k+5] == '"10"' or content[k:k+5] == '"11"' or content[k:k+5] == '"12"' or content[k:k+5] == '"13"' or content[k:k+5] == '"14"' or content[k:k+5] == '"15"' or content[k:k+4] == '"ma' or content[k:k+4] == '"do'):
                    is_closing = True
            if is_closing:
                in_string = False
                quote_char = None
                result.append(c)
            else:
                result.append("\\")
                result.append(c)
            i += 1
            continue
        if in_string and c == "\n":
            result.append("\\n")
            i += 1
            continue
        if in_string and c == "\r":
            result.append("\\r")
            i += 1
            continue
        if in_string and c == "\t":
            result.append("\\t")
            i += 1
            continue
        result.append(c)
        i += 1

    return "".join(result)


def main():
    path = "assignments.json"
    with open(path, encoding="utf-8") as f:
        content = f.read()

    fixed = fix_json_strings(content)
    data = json.loads(fixed)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("Готово! assignments.json исправлен.")


if __name__ == "__main__":
    main()
