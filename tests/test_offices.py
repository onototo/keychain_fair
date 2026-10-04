from keychain_fair.offices import Office, search_offices


OFFICES = (
    Office("1", "1", "Минск", "ул. Брестская, 1"),
    Office("2", "2", "Брест", "ул. Минская, 2"),
    Office("3", "10", "Брест", "ул. Советская, 3"),
)


def test_city_match_ranks_ahead_of_an_address_match():
    found = search_offices(OFFICES, "  брест ")
    assert [office.number for office in found] == ["2", "10", "1"]


def test_address_search_and_short_query():
    assert search_offices(OFFICES, "минская")[0].id == "2"
    assert search_offices(OFFICES, "м") == ()


def test_search_returns_at_most_24_offices():
    offices = tuple(Office(str(index), str(index), "Минск", f"ул. {index}") for index in range(1, 40))
    assert len(search_offices(offices, "Минск")) == 24
