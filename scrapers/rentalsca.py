class RentalsСaScraper:
    """Scraper for Rentals.ca listings."""

    def __init__(self, session, log_buffer, proxy):
        self.session = session
        self.log_buffer = log_buffer
        self.proxy = proxy
        self.new_count = 0
        self.updated_count = 0
        self.error_count = 0

    async def run(self):
        pass
