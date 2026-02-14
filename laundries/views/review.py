# pyre-ignore[missing-module]
from rest_framework import generics, permissions, status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from ..serializers.review import ReviewSerializer
# pyre-ignore[missing-module]
from ..models.laundry import Laundry

class ReviewCreateView(generics.CreateAPIView):
    serializer_class = ReviewSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        laundry_id = self.kwargs.get('laundry_id')
        laundry = Laundry.objects.get(id=laundry_id)
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
