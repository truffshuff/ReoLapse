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


def test_nms_time_limit_is_off():
    """A batch slower than Ultralytics' NMS time limit must keep every image's detections."""
    import torch
    from ultralytics.utils import nms

    from reolapse.analyze import _no_nms_time_limit

    _no_nms_time_limit()
    # 8 images, 2 classes, one confident box each: xywh + 2 class scores, (bs, 4 + nc, anchors)
    pred = torch.zeros(8, 6, 4)
    pred[:, :4, 0] = torch.tensor([50.0, 50.0, 20.0, 20.0])
    pred[:, 4, 0] = 0.9
    out = nms.non_max_suppression(pred, 0.25, 0.7, nc=2, max_time_img=-1.0)  # a limit that's always exceeded
    assert [len(o) for o in out] == [1] * 8
