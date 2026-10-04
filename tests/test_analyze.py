from collections import Counter

from reolapse.analyze import vote


def test_person_wins_over_animal_from_a_third():
    assert vote(Counter(person=12, bird=8, dog=2)) == "person"  # Oct 1, 19:16: bent over, cut off, IR
    assert vote(Counter(person=8, dog=16)) == "person"  # a third is enough
    assert vote(Counter(person=3, dog=16)) == "animal"  # a dog with a few stray "person" frames
    assert vote(Counter(cat=10, dog=9, person=6)) == "animal"  # animal classes add up first


def test_vote_by_category_not_class():
    assert vote(Counter(car=5, truck=4, person=6)) == "vehicle"
    assert vote(Counter(person=5, bicycle=6)) == "vehicle"  # only animals defer to people
