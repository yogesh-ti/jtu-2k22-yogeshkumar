# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from decimal import Decimal
import urllib.request
from datetime import datetime
import logging

from django.http import HttpResponse
from django.contrib.auth.models import User

# Create your views here.
from rest_framework.permissions import AllowAny
from rest_framework.decorators import api_view, action, authentication_classes, permission_classes
from rest_framework.viewsets import ModelViewSet
from rest_framework.response import Response
from rest_framework import status

import restapi.constants
from restapi.models import Category, Group, Expense, UserExpense
from restapi.serializers import UserSerializer, CategorySerializer, GroupSerializer, ExpensesSerializer
from restapi.custom_exception import UnauthorizedUserException
from restapi.utils import multi_threaded_reader, sort_by_time_stamp, transform, aggregate, response_format, normalize

logger = logging.getLogger(__name__)

def index(_request):
    return HttpResponse("Hello, world. You're at Rest.")


@api_view(['POST'])
def logout(request):
    request.user.auth_token.delete()
    logger.info('User has logged out')
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
def balance(request):
    start_time = datetime.now()

    user = request.user
    expenses = Expense.objects.filter(users__in=user.expenses.all())
    final_balance = {}
    for expense in expenses:
        expense_balances = normalize(expense)
        for eb in expense_balances:
            from_user = eb['from_user']
            to_user = eb['to_user']
            if from_user == user.id:
                final_balance[to_user] = final_balance.get(to_user, 0) - eb['amount']
            if to_user == user.id:
                final_balance[from_user] = final_balance.get(from_user, 0) + eb['amount']
    final_balance = {k: v for k, v in final_balance.items() if v != 0}

    response = [{"user": k, "amount": int(v)} for k, v in final_balance.items()]

    time_taken = datetime().now() - start_time

    logger.info(f'Balance request process time: {int(time_taken.total_seconds() * 1000)} ms')
    return Response(response, status=status.HTTP_200_OK)

class UserViewSet(ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = (AllowAny,)


class CategoryViewSet(ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    http_method_names = ['get', 'post']


class GroupViewSet(ModelViewSet):
    queryset = Group.objects.all()
    serializer_class = GroupSerializer

    def get_queryset(self):
        user = self.request.user
        groups = user.members.all()
        if self.request.query_params.get('q', None) is not None:
            groups = groups.filter(name__icontains=self.request.query_params.get('q', None))
        return groups

    def create(self, request, *args, **kwargs):
        user = self.request.user
        data = self.request.data
        group = Group(**data)
        group.save()
        group.members.add(user)
        serializer = self.get_serializer(group)
        return Response(serializer.data, status=status.HTTP_201)

    @action(methods=['put'], detail=True)
    def members(self, request, pk=None):
        group = Group.objects.get(id=pk)
        if group not in self.get_queryset():
            raise UnauthorizedUserException()
        body = request.data
        if body.get('add', None) is not None and body['add'].get('user_ids', None) is not None:
            added_ids = body['add']['user_ids']
            for user_id in added_ids:
                group.members.add(user_id)
        if body.get('remove', None) is not None and body['remove'].get('user_ids', None) is not None:
            removed_ids = body['remove']['user_ids']
            for user_id in removed_ids:
                group.members.remove(user_id)
        group.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(methods=['get'], detail=True)
    def expenses(self, _request, pk=None):
        start_time = datetime.now()

        group = Group.objects.get(id=pk)
        if group not in self.get_queryset():
            raise UnauthorizedUserException()
        expenses = group.expenses_set
        serializer = ExpensesSerializer(expenses, many=True)

        time_taken = datetime.now() - start_time

        logger.info(f'Expense request process time: {int(time_taken.total_seconds() * 1000)} ms')
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(methods=['get'], detail=True)
    def balances(self, _request, pk=None):
        start_time = datetime.now()

        group = Group.objects.get(id=pk)
        if group not in self.get_queryset():
            raise UnauthorizedUserException()
        expenses = Expense.objects.filter(group=group)
        balances = normalize(expenses)

        time_taken = datetime.now() - start_time

        logger.info(f'Group balance request process time: {int(time_taken.total_seconds() * 1000)} ms')
        return Response(balances, status=status.HTTP_200_OK)


class ExpenseViewSet(ModelViewSet):
    queryset = Expense.objects.all()
    serializer_class = ExpensesSerializer

    def get_queryset(self):
        user = self.request.user
        if self.request.query_params.get('q', None) is not None:
            expenses = Expense.objects.filter(users__in=user.expenses.all())\
                .filter(description__icontains=self.request.query_params.get('q', None))
        else:
            expenses = Expense.objects.filter(users__in=user.expenses.all())
        return expenses

@api_view(['post'])
@authentication_classes([])
@permission_classes([])
def log_processor(request):
    data = request.data
    num_threads = data['parallelFileProcessingCount']
    log_files = data['logFiles']
    if num_threads <= 0 or num_threads > 30:
        return Response({"status": "failure", "reason": "Parallel Processing Count out of expected bounds"},
                        status=status.HTTP_400_BAD_REQUEST)
    if len(log_files) == 0:
        return Response({"status": "failure", "reason": "No log files provided in request"},
                        status=status.HTTP_400_BAD_REQUEST)
    logs = multi_threaded_reader(urls=data['logFiles'], num_threads=data['parallelFileProcessingCount'])
    sorted_logs = sort_by_time_stamp(logs)
    cleaned = transform(sorted_logs)
    data = aggregate(cleaned)
    response = response_format(data)
    return Response({"response":response}, status=status.HTTP_200_OK)