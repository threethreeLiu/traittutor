export interface SelectedBookPage {
  bookId: string;
  bookTitle: string;
  pageId: string;
  pageTitle: string;
  chapterId?: string;
  chapterTitle?: string;
}

export interface SelectedBookReference {
  bookId: string;
  bookTitle: string;
  pages: SelectedBookPage[];
}

export interface BookReferencePayload {
  book_id: string;
  page_ids: string[];
}

export function selectedBooksToPayload(refs: SelectedBookReference[]): BookReferencePayload[] {
  return refs.map((ref) => {
    const payload = { book_id: ref.bookId, page_ids: ref.pages.map((page) => page.pageId) };
    if (!payload.book_id || payload.page_ids.length === 0 || payload.page_ids.some((id) => !id)) {
      throw new Error("Invalid book reference: book_id and page_ids are required");
    }
    return payload;
  });
}

export function normalizeBookReferences(value: unknown): BookReferencePayload[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) {
    throw new Error("Invalid book references: expected an array");
  }
  return value.map((item) => {
    if (
      !item ||
      typeof item !== "object" ||
      typeof (item as BookReferencePayload).book_id !== "string" ||
      !(item as BookReferencePayload).book_id ||
      !Array.isArray((item as BookReferencePayload).page_ids) ||
      (item as BookReferencePayload).page_ids.some((pageId) => typeof pageId !== "string" || !pageId)
    ) {
      throw new Error("Invalid book reference: book_id and page_ids are required");
    }
    return item as BookReferencePayload;
  });
}
