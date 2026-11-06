"""Shared sequential PDFium validation and previews of actual generated PDFs."""

from contextlib import closing


def pdf_previews(root, target, folder, expected_pages=None):
    import pypdfium2 as pdfium

    previews = []
    with pdfium.PdfDocument(str(target)) as pdf:
        if expected_pages is not None and len(pdf) != expected_pages:
            raise ValueError("Количество страниц PDF не совпадает")
        if not 1 <= len(pdf) <= 600:
            raise ValueError("Недопустимое количество страниц PDF")
        for number in range(len(pdf)):
            with closing(pdf[number]) as page:
                with closing(page.get_textpage()) as textpage:
                    if not textpage.get_text_range().strip():
                        raise ValueError("PDF содержит пустую страницу")
                bitmap = page.render(scale=1.5)
                image = bitmap.to_pil()
                name = f"page-{number + 1}.png"
                image.save(folder / name)
                image.thumbnail((320, 180))
                thumb = f"thumb-{number + 1}.png"
                image.save(folder / thumb)
                bitmap.close()
                previews.append(
                    {
                        "page": number + 1,
                        "preview": str((folder / name).relative_to(root)).replace("\\", "/"),
                        "thumbnail": str((folder / thumb).relative_to(root)).replace("\\", "/"),
                    }
                )
    return previews
