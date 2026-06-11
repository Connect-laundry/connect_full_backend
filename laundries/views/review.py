# pyre-ignore[missing-module]
from rest_framework import generics, permissions, status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.shortcuts import get_object_or_404
# pyre-ignore[missing-module]
from ..serializers.review import ReviewSerializer
# pyre-ignore[missing-module]
from ..models.laundry import Laundry
# pyre-ignore[missing-module]
from ..models.review import Review

class ReviewCreateView(generics.CreateAPIView):
    serializer_class = ReviewSerializer
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'review'

    def perform_create(self, serializer):
        laundry = get_object_or_404(Laundry, id=self.kwargs.get('laundry_id'))
        user = self.request.user

        from ordering.models import Order
        has_completed_order = Order.objects.filter(
            user=user,
            laundry=laundry,
            status__in=[Order.Status.DELIVERED, Order.Status.COMPLETED],
        ).exists()
        if not has_completed_order:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(
                'You can only review a laundry you have completed an order with.'
            )

        if Review.objects.filter(laundry=laundry, user=user).exists():
            from rest_framework.exceptions import ValidationError
            raise ValidationError('You have already reviewed this laundry.')

        serializer.save(laundry=laundry)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response({
            "status": "success",
            "message": "Review submitted successfully.",
            "data": serializer.data
        }, status=status.HTTP_201_CREATED)
