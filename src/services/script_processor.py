import re
import logging

logger = logging.getLogger(__name__)


class ScriptProcessor:
    """
    Processes raw script text into normalized sentences for TTS.
    - Splits text into sentences
    - Auto-merges sentences < 5 words
    - Auto-splits sentences > 20 words
    """

    def __init__(self, min_words: int = 5, max_words: int = 20):
        self.min_words = min_words
        self.max_words = max_words

    def process(self, script: str) -> list[str]:
        """
        Process raw script text and return list of normalized sentences.

        Args:
            script: Raw text input

        Returns:
            List of normalized sentence strings
        """
        logger.info(f"Processing script ({len(script)} characters)")

        # Normalize whitespace and remove extra spaces
        script = " ".join(script.split())

        # Split into initial sentences
        sentences = self._split_sentences(script)

        # Merge short sentences
        sentences = self._merge_short_sentences(sentences)

        # Split long sentences
        sentences = self._split_long_sentences(sentences)

        # Final cleanup
        sentences = [s.strip() for s in sentences if s.strip()]

        logger.info(f"Processed script into {len(sentences)} sentences")
        return sentences

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences using regex"""
        # Split on sentence-ending punctuation followed by space and capital letter
        # Also handle common abbreviations
        pattern = r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?|!)\s+(?=[A-Z])'
        sentences = re.split(pattern, text)
        return sentences

    def _merge_short_sentences(self, sentences: list[str]) -> list[str]:
        """Merge sentences that are too short"""
        if not sentences:
            return []

        merged = []
        buffer = ""

        for sentence in sentences:
            word_count = len(sentence.split())

            if buffer:
                # Add to buffer
                buffer = f"{buffer} {sentence}"
                buffer_word_count = len(buffer.split())

                # If buffer is now long enough, add it
                if buffer_word_count >= self.min_words:
                    merged.append(buffer)
                    buffer = ""
            elif word_count < self.min_words:
                # Start buffer
                buffer = sentence
            else:
                # Sentence is fine as-is
                merged.append(sentence)

        # Add any remaining buffer
        if buffer:
            if merged:
                # Merge with last sentence
                merged[-1] = f"{merged[-1]} {buffer}"
            else:
                merged.append(buffer)

        return merged

    def _split_long_sentences(self, sentences: list[str]) -> list[str]:
        """Split sentences that are too long at natural breaking points"""
        result = []

        for sentence in sentences:
            word_count = len(sentence.split())

            if word_count <= self.max_words:
                result.append(sentence)
            else:
                # Split on commas, semicolons, or conjunctions
                # Try comma first
                parts = self._split_at_delimiter(sentence, ',')

                if not parts or max(len(p.split()) for p in parts) > self.max_words:
                    # Try semicolon
                    parts = self._split_at_delimiter(sentence, ';')

                if not parts or max(len(p.split()) for p in parts) > self.max_words:
                    # Try conjunctions (and, but, or)
                    parts = self._split_at_conjunctions(sentence)

                if parts:
                    result.extend(parts)
                else:
                    # Can't split naturally, just add as-is
                    result.append(sentence)

        return result

    def _split_at_delimiter(self, text: str, delimiter: str) -> list[str]:
        """Split text at delimiter and validate parts"""
        parts = [p.strip() for p in text.split(delimiter)]

        # Check if all parts are reasonable length
        valid_parts = []
        for i, part in enumerate(parts):
            word_count = len(part.split())
            if word_count >= self.min_words or i == len(parts) - 1:
                # Restore delimiter except for last part
                if i < len(parts) - 1:
                    part = f"{part}{delimiter}"
                valid_parts.append(part)

        # Only return if we got valid splits
        if len(valid_parts) > 1 and all(len(p.split()) <= self.max_words for p in valid_parts):
            return valid_parts

        return []

    def _split_at_conjunctions(self, text: str) -> list[str]:
        """Split text at coordinating conjunctions"""
        # Split at common conjunctions with word boundaries
        pattern = r'\s+(and|but|or|yet|so)\s+'
        parts = re.split(pattern, text, flags=re.IGNORECASE)

        if len(parts) < 3:  # Need at least text + conjunction + text
            return []

        # Reconstruct with conjunctions
        result = []
        i = 0
        while i < len(parts):
            if i + 2 < len(parts):
                # Combine part + conjunction + next part
                combined = f"{parts[i].strip()} {parts[i+1]} {parts[i+2].strip()}"
                result.append(combined)
                i += 3
            else:
                result.append(parts[i].strip())
                i += 1

        # Validate parts
        if all(self.min_words <= len(p.split()) <= self.max_words for p in result):
            return result

        return []


# Singleton instance
script_processor = ScriptProcessor()
